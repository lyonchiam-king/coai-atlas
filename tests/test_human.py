"""Human pacing and per-lead messages."""
import calendar
import random
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

import pytest

from atlas.agents import Ctx
from atlas.agents.writer import Writer, check_message
from atlas.config import Config
from atlas.llm import ClaudeLLM, LLMError
from atlas.models import Stage
from atlas.pacing import Pacer
from atlas.store import Store
from atlas.swarm import Swarm
from test_swarm import Clock, FakeChannel

MYT = timezone(timedelta(hours=8))


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=MYT).timestamp()


MON = (2026, 10, 5)   # a Monday
FRI = (2026, 10, 9)
SUN = (2026, 10, 11)


class RecordingChannel(FakeChannel):
    def __init__(self, clock):
        super().__init__()
        self.clock, self.log = clock, []

    def send(self, phone, text, typing_ms=0):
        super().send(phone, text, typing_ms)
        self.log.append((self.clock.t, typing_ms))


def simulate_day(day, leads=30, cap=20, seed=1):
    store, clock = Store(), Clock(at(*day, 7, 0))
    for i in range(leads):
        store.add_lead(f"Lead{i}", f"+601234{i:05d}", f"Shop {i}", {"industry": "bakery"})
    ch = RecordingChannel(clock)
    swarm = Swarm(store, ch, Config(daily_cap=cap, two_step=False), None, clock, rng=random.Random(seed))
    while clock.t < at(*day, 22, 0):
        swarm.tick()
        clock.t += 60                      # auto-run wakes every minute
    return store, ch, swarm.pacer.plan(at(*day, 12))


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_a_simulated_monday_looks_like_a_person(seed):
    store, ch, plan = simulate_day(MON, seed=seed)
    times = [t for t, _ in ch.log]
    assert 12 <= len(times) <= 20                       # quota is 60-100% of the cap of 20
    assert len(times) == plan.quota or times[-1] >= plan.end - 30 * 60
    for t in times:
        assert plan.start <= t < plan.end               # inside the day's window
        assert not plan.lunch_start <= t < plan.lunch_end
    gaps = [b - a for a, b in zip(times, times[1:]) if not (a < plan.lunch_start <= b)]
    assert min(gaps) >= 3 * 60                          # never machine-gunned
    assert len({round(g / 60) for g in gaps}) >= 4      # not a metronome
    assert all(4_000 <= ms <= 18_000 for _, ms in ch.log)


def test_each_day_starts_at_a_different_time_and_sends_a_different_amount():
    plans = [simulate_day(MON, seed=s)[2] for s in range(1, 7)]
    assert len({round(p.start / 60) for p in plans}) >= 4
    assert len({p.quota for p in plans}) >= 2
    for p in plans:
        local = datetime.fromtimestamp(p.start, MYT)
        assert (9, 15) <= (local.hour, local.minute) <= (10, 30)


def test_sunday_is_off():
    _, ch, plan = simulate_day(SUN)
    assert ch.log == [] and plan.working is False


def test_friday_lunch_covers_jumaat():
    _, ch, plan = simulate_day(FRI, seed=3)
    for t, _ in ch.log:
        assert not at(*FRI, 12, 15) <= t < at(*FRI, 14, 45)


def test_the_days_plan_survives_a_restart():
    store = Store()
    cfg = Config()
    a = Pacer(store, cfg, random.Random(1)).plan(at(*MON, 11))
    b = Pacer(store, cfg, random.Random(999)).plan(at(*MON, 15))   # new process, new rng
    assert a == b


def test_gap_distribution():
    p = Pacer(Store(), Config(), random.Random(7))
    gaps = [p.gap_seconds() / 60 for _ in range(2000)]
    assert min(gaps) >= 3 and max(gaps) <= 50
    assert 5 <= statistics.median(gaps) <= 9
    assert 0.07 <= sum(g >= 25 for g in gaps) / len(gaps) <= 0.2   # the occasional longer break


def test_stage_dead_draft_does_not_use_up_the_turn():
    store, clock = Store(), Clock(at(*MON, 7))
    a = store.add_lead("A", "+60123450001")
    store.add_lead("B", "+60123450002")
    swarm = Swarm(store, ch := FakeChannel(), Config(two_step=False), None, clock, rng=random.Random(1))
    swarm.tick()                                         # 07:00: drafts written, nothing sent
    assert ch.sent == [] and len(store.pending_drafts()) == 2
    store.set_stage(a, Stage.DO_NOT_CONTACT)             # A says STOP before their turn
    clock.t = swarm.pacer.plan(clock.t).start + 1
    swarm.tick()
    assert [p for p, _ in ch.sent] == ["+60123450002"]   # B still goes in the same turn


# ---- writer -----------------------------------------------------------------

class FakeLLM:
    name = "fake"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, prompt):
        self.calls.append((system, prompt))
        r = self.replies.pop(0) if self.replies else "x"
        if isinstance(r, Exception):
            raise r
        return r


GOOD = ("Hi Aisha, saw that Kedai Aisha in Bayan Lepas has a 4.8 rating but no website yet. "
        "We help bakeries turn that reputation into WhatsApp orders. Would a quick chat help?")


def ctx_with(llm, store=None):
    return Ctx(store or Store(), Config(human_pacing=False, two_step=False), llm, Clock(), random.Random(3))


def test_what_we_know_reaches_the_model_and_the_language_is_honoured():
    llm = FakeLLM(GOOD)
    c = ctx_with(llm)
    c.store.add_lead("Aisha Rahman", "+60123456789", "Kedai Aisha",
                     {"industry": "bakery", "area": "Bayan Lepas", "notes": "4.8 stars, no website", "language": "zh"})
    rep = Writer().run(c)
    system, prompt = llm.calls[0]
    for fact in ("Kedai Aisha", "Industry: bakery", "Bayan Lepas", "4.8 stars, no website", "Aisha Rahman"):
        assert fact in prompt
    assert "Simplified Chinese" in system
    (_, d), = c.store.pending_drafts()
    assert d.source == "ai" and d.text.startswith(GOOD)
    assert d.text.endswith("Reply STOP and I won't message again.")
    assert d.text.count("STOP") == 1 and rep.skipped.get("ai_rejected_by_checks") is None


def test_a_bad_ai_answer_is_retried_then_falls_back_to_a_template():
    llm = FakeLLM("Hi! Check https://x.y", "Hi Aisha, great bakery. Reply STOP to opt out of these.")
    c = ctx_with(llm)
    c.store.add_lead("Aisha", "+60123456789", "Kedai Aisha", {"area": "Bayan Lepas"})
    rep = Writer().run(c)
    (_, d), = c.store.pending_drafts()
    assert len(llm.calls) == 2 and d.source == "template"
    assert "Kedai Aisha" in d.text and rep.skipped["ai_rejected_by_checks"] == 1


def test_an_api_failure_falls_back_and_says_why():
    c = ctx_with(FakeLLM(LLMError("the API key was rejected")))
    c.store.add_lead("Aisha", "+60123456789")
    rep = Writer().run(c)
    assert rep.done == 1 and rep.skipped == {"ai_failed: the API key was rejected": 1}


def test_follow_up_prompt_carries_the_previous_message():
    llm = FakeLLM(GOOD)
    c = ctx_with(llm)
    lid = c.store.add_lead("Aisha", "+60123456789")
    c.store.mark_sent(lid, Stage.CONTACTED, c.now() - 10 * 86400, "Hi Aisha, first note about cakes.")
    Writer().run(c)
    assert "first note about cakes" in llm.calls[0][1]
    assert c.store.pending_drafts()[0][1].kind == "followup_1"


def test_checks():
    cfg = Config()
    assert check_message(GOOD, cfg) == GOOD
    assert check_message("We are a one-stop centre for bakeries in Penang, happy to help anytime.", cfg)
    for bad in ("short", "Hi [Name], we help businesses grow with AI tools today, keen?",
                GOOD + " www.coai.my", GOOD + " Reply stop to opt out.", "x" * 800):
        assert check_message(bad, cfg) is None


def test_templates_use_the_facts_and_vary():
    c = ctx_with(None)
    for i in range(12):
        c.store.add_lead(f"Owner{i}", f"+6012345{i:04d}", f"Shop{i}", {"industry": "florist", "area": "Georgetown"})
    Writer().run(c)
    texts = [d.text for _, d in c.store.pending_drafts()]
    assert all("Shop" in t for t in texts)
    assert sum("Georgetown" in t for t in texts) == 12 and sum("florist" in t for t in texts) == 12
    openings = {t.split(",")[0].split("!")[0].split()[0] for t in texts}
    assert len(openings) >= 2                                  # more than one wording


def test_private_notes_never_appear_in_a_template_but_a_hook_does():
    c = ctx_with(None)
    c.store.add_lead("Aisha", "+60123456789", "Kedai Aisha",
                     {"notes": "owner rude on the phone", "hook": "Your kaya puffs came up twice in reviews I read"})
    Writer().run(c)
    text = c.store.pending_drafts()[0][1].text
    assert "rude" not in text and "kaya puffs came up twice" in text


def test_drafts_are_not_written_far_ahead_of_sending():
    c = ctx_with(None)
    c.cfg.max_queued_drafts = 5
    for i in range(12):
        c.store.add_lead(f"L{i}", f"+6012345{i:04d}")
    rep = Writer().run(c)
    assert rep.done == 5 and rep.skipped["queue_full"] == 7


# ---- the Claude connection ----------------------------------------------------

class FakeBlock:
    def __init__(self, type, text=""):
        self.type, self.text = type, text


class FakeMsg:
    def __init__(self, stop_reason, *blocks):
        self.stop_reason, self.content = stop_reason, list(blocks)


class FakeClient:
    def __init__(self, msg):
        self.msg, self.kwargs = msg, None
        outer = self

        class Messages:
            def create(self, **kw):
                outer.kwargs = kw
                return outer.msg

        class Beta:
            messages = Messages()

        self.beta = Beta()


def test_claude_request_shape_and_text_extraction():
    pytest.importorskip("anthropic")
    fc = FakeClient(FakeMsg("end_turn", FakeBlock("thinking"), FakeBlock("text", "Hello there")))
    out = ClaudeLLM("claude-opus-5-5", fc).complete("sys", "prompt")
    assert out == "Hello there"
    kw = fc.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"] == {"effort": "low"} and kw["system"] == "sys"


def test_a_refusal_is_never_sent_as_a_message():
    pytest.importorskip("anthropic")
    fc = FakeClient(FakeMsg("refusal", FakeBlock("text", "I can't help with that")))
    with pytest.raises(LLMError, match="declined"):
        ClaudeLLM("m", fc).complete("s", "p")


def test_from_env_reads_dot_env_and_needs_a_key(tmp_path, monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ClaudeLLM.from_env("m", tmp_path / ".env") is None
    (tmp_path / ".env").write_text('# key\nANTHROPIC_API_KEY="sk-test"\n')
    assert ClaudeLLM.from_env("m", tmp_path / ".env").model == "m"


# ---- an atlas.db made by the previous version --------------------------------

def test_an_older_database_is_upgraded_in_place(tmp_path):
    db = tmp_path / "atlas.db"
    old = sqlite3.connect(db)
    old.executescript("""
      CREATE TABLE leads(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT UNIQUE, company TEXT DEFAULT '',
        stage TEXT DEFAULT 'new', notes TEXT DEFAULT '', last_contacted REAL);
      CREATE TABLE drafts(id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, text TEXT, kind TEXT, sent INTEGER DEFAULT 0);
      CREATE TABLE inbound(id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, text TEXT, at REAL, intent TEXT DEFAULT '');
      CREATE TABLE sends(id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, at REAL);
      INSERT INTO leads(name, phone) VALUES ('Old', '+60111111111');""")
    old.commit(); old.close()
    s = Store(str(db))
    assert s.lead(1).facts == {}
    lid = s.add_lead("New", "+60122222222", "", {"industry": "cafe"})
    s.mark_sent(lid, Stage.CONTACTED, 1.0, "hi", "ai")
    from atlas.models import Inbound
    assert s.add_inbound(Inbound(lid, "x", 1.0), "W1") and not s.add_inbound(Inbound(lid, "x", 1.0), "W1")
    assert s.lead(lid).facts == {"industry": "cafe"}
