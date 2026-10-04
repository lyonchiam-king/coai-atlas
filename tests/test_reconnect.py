"""The two-step reconnect, three phones, and the WhatsApp number check."""
import random

import pytest

from atlas.agents import Ctx
from atlas.agents.responder import classify_confirm
from atlas.agents.writer import Writer
from atlas.config import Config
from atlas.models import Inbound, Stage
from atlas.store import Store
from atlas.swarm import Swarm
from test_human import FakeLLM, MON, at
from test_swarm import Clock, FakeChannel

DAY = 86400
OPT = "Reply STOP and I won't message again."


def setup(cfg=None, llm=None, **lead):
    store, clock, ch = Store(), Clock(at(*MON, 11)), FakeChannel()
    lid = store.add_lead(lead.get("name", "Aisha Rahman"), "+60123456789", "Kedai Aisha",
                         lead.get("facts", {"how_we_know": "Penang Rotary"}))
    swarm = Swarm(store, ch, cfg or Config(human_pacing=False), llm, clock, rng=random.Random(2))
    return store, clock, ch, swarm, lid


def reply(store, lid, text, t):
    store.add_inbound(Inbound(lid, text, t))


def test_opener_checks_the_number_and_sells_nothing():
    store, clock, ch, swarm, lid = setup()
    swarm.tick()
    (phone, text), = ch.sent
    assert "Aisha" in text and "Lyon" in text and "Penang Rotary" in text
    assert "COAI" not in text and "STOP" not in text          # no pitch, no robot footer
    assert store.lead(lid).stage is Stage.OPENER_SENT


def test_a_confirmation_gets_the_pitch_as_a_reply():
    store, clock, ch, swarm, lid = setup()
    swarm.tick()
    clock.t += 3600
    reply(store, lid, "Yes! Long time Lyon, how are you?", clock.t)
    swarm.tick()
    assert store.lead(lid).stage is Stage.CONTACTED
    pitch = ch.sent[-1][1]
    assert "COAI" in pitch and pitch.endswith(OPT)
    clock.t += 3 * DAY
    swarm.tick()
    assert store.lead(lid).stage is Stage.FOLLOWUP_1      # the usual follow-ups after the pitch


def test_the_pitch_answers_what_they_said():
    llm = FakeLLM("Hi Lyon, it's me!", "I'm good thanks! Glad to hear the bakery is busy. I've started COAI, "
                  "we build AI tools that help SMEs get more customers. Could it help Kedai Aisha?")
    store, clock, ch, swarm, lid = setup(llm=llm)
    swarm.tick()                                             # opener: first FakeLLM reply
    reply(store, lid, "Yes la, still me! Bakery very busy now", clock.t)
    swarm.tick()
    system, prompt = llm.calls[-1]
    assert "Bakery very busy now" in prompt and "COAI" in system
    assert ch.sent[-1][1].startswith("I'm good thanks!")


@pytest.mark.parametrize("text", ["Sorry wrong number", "salah nombor", "打错了", "I don't know you"])
def test_wrong_number_is_never_written_to_again(text):
    store, clock, ch, swarm, lid = setup()
    swarm.tick()
    reply(store, lid, text, clock.t)
    for _ in range(5):
        clock.t += 10 * DAY
        swarm.tick()
    assert store.lead(lid).stage is Stage.WRONG_NUMBER and len(ch.sent) == 1


@pytest.mark.parametrize("text", ["Yes, who is this?", "Siapa ni?", "你是谁？", "hi"])
def test_who_is_this_goes_to_a_person_not_a_pitch(text):
    store, clock, ch, swarm, lid = setup()
    swarm.tick()
    reply(store, lid, text, clock.t)
    swarm.tick()
    assert store.lead(lid).stage is Stage.REPLIED and len(ch.sent) == 1


def test_an_ai_classifier_decides_what_the_rules_cannot():
    c = Ctx(Store(), Config(), FakeLLM("CONFIRM"), Clock())
    assert classify_confirm("Oh hey! Been ages, how's things", c) == "confirm"
    c = Ctx(Store(), Config(), FakeLLM("banana"), Clock())
    assert classify_confirm("Oh hey! Been ages", c) == "unsure"   # nonsense from a model is never a yes


def test_one_nudge_then_silence_and_a_late_yes_still_works():
    store, clock, ch, swarm, lid = setup()
    swarm.tick()
    clock.t += 2 * DAY
    swarm.tick()
    assert len(ch.sent) == 1                                 # too early to nudge
    clock.t += 2 * DAY                                       # 4 days: past 3 + jitter
    swarm.tick()
    assert store.lead(lid).stage is Stage.OPENER_NUDGED and len(ch.sent) == 2
    assert "COAI" not in ch.sent[1][1]
    clock.t += 30 * DAY
    swarm.tick()
    assert len(ch.sent) == 2                                 # nothing more, ever, unless they answer
    reply(store, lid, "betul, sorry baru nampak", clock.t)
    swarm.tick()
    assert store.lead(lid).stage is Stage.CONTACTED and "COAI" in ch.sent[-1][1]


def test_stop_in_answer_to_the_opener_is_final():
    store, clock, ch, swarm, lid = setup()
    swarm.tick()
    reply(store, lid, "stop messaging me", clock.t)
    clock.t += 10 * DAY
    swarm.tick()
    assert store.lead(lid).stage is Stage.DO_NOT_CONTACT and len(ch.sent) == 1


@pytest.mark.parametrize("lang,expect", [("ms", "nombor"), ("zh", "号码"), ("en", "number")])
def test_openers_in_the_contacts_language_without_ai(lang, expect):
    store, clock, ch, swarm, lid = setup(facts={"language": lang})
    swarm.tick()
    assert expect in ch.sent[0][1]


def test_an_ai_opener_that_sells_is_replaced():
    llm = FakeLLM("Hi Aisha! Lyon here, I started COAI and we build AI tools.", "Hi Aisha, Lyon from COAI here!")
    store, clock, ch, swarm, lid = setup(llm=llm)
    rep = {r.agent: r for r in swarm.tick()}["writer"]
    assert "COAI" not in ch.sent[0][1] and rep.skipped["ai_rejected_by_checks"] == 1


def test_a_reply_jumps_the_quota():
    store, clock, ch, swarm, lid = setup(cfg=Config())
    pacer = swarm.pacer
    plan = pacer.plan(clock.t)
    clock.t = max(plan.start + 60, clock.t)
    if plan.lunch_start <= clock.t < plan.lunch_end:
        clock.t = plan.lunch_end + 60
    other = store.add_lead("Filler", "+60100000000")
    for i in range(plan.quota):                              # today's quota already used
        store.mark_sent(other, Stage.CONTACTED, clock.t - 3600 - i, "x")
    assert pacer.ready(clock.t) == (False, "quota_reached")
    store.set_stage(lid, Stage.CONFIRMED)
    reply(store, lid, "yes it's me", clock.t)
    swarm.tick()
    assert ch.sent and "COAI" in ch.sent[-1][1]              # someone who answered is answered


# ---- three phones ----------------------------------------------------------------

def test_each_phone_writes_only_to_its_own_list_and_has_its_own_cap():
    store, clock = Store(), Clock(at(*MON, 11))
    phones = {a: FakeChannel() for a in ("1", "2", "3")}
    for a in phones:
        for i in range(4):
            store.add_lead(f"P{a}-{i}", f"+60123{a}{i:05d}", "", {}, account=a, list_name=f"list{a}")
    swarm = Swarm(store, cfg=Config(human_pacing=False, daily_cap=3), now=clock, channels=phones)
    swarm.tick()
    for a, ch in phones.items():
        assert len(ch.sent) == 3                             # each phone's own cap
        assert all(p.startswith(f"+60123{a}") for p, _ in ch.sent)


def test_a_stop_sent_to_a_different_phone_still_stops():
    class Relay(FakeChannel):
        def __init__(self):
            super().__init__()
            self.box, self.acked = [], []
        def poll(self): return self.box
        def ack(self, ids): self.acked += ids; self.box = []

    store, clock = Store(), Clock(at(*MON, 11))
    p1, p2 = Relay(), Relay()
    lid = store.add_lead("Aisha", "+60123456789", account="1")
    swarm = Swarm(store, cfg=Config(human_pacing=False), now=clock, channels={"1": p1, "2": p2})
    swarm.tick()
    p2.box = [{"id": "W1", "phone": "+60123456789", "text": "STOP", "at": clock.t}]
    swarm.tick()
    assert store.lead(lid).stage is Stage.DO_NOT_CONTACT


def test_duplicate_numbers_across_lists_stay_with_the_first_phone():
    store = Store()
    assert store.add_lead("A", "+60123456789", account="1") is not None
    assert store.add_lead("A again", "+60123456789", account="2") is None
    assert store.lead(1).account == "1"


# ---- the WhatsApp check ----------------------------------------------------------

class CheckingRelay(FakeChannel):
    def __init__(self, verdicts, profile=None):
        super().__init__()
        self.verdicts, self.asked, self.prof = verdicts, [], profile or {}

    def check(self, phones):
        self.asked.append(list(phones))
        return {p: {"registered": self.verdicts.get(p)} for p in phones}

    def profile(self, phone):
        return self.prof


def test_numbers_are_checked_before_anything_is_written():
    store, clock = Store(), Clock(at(*MON, 11))
    yes = store.add_lead("Yes", "+60111111111")
    no = store.add_lead("No", "+60122222222")
    unknown = store.add_lead("Unknown", "+60133333333")
    relay = CheckingRelay({"+60111111111": True, "+60122222222": False},
                          {"about": "Baking since 2010", "business": {"category": "Bakery", "website": "kedai.my"}})
    swarm = Swarm(store, relay, Config(human_pacing=False), None, clock)
    swarm.tick()
    assert [p for p, _ in relay.sent] == ["+60111111111"]
    assert store.lead(no).stage is Stage.NOT_ON_WHATSAPP
    assert store.lead(unknown).stage is Stage.NEW and store.lead(unknown).wa == ""   # asked again later
    f = store.lead(yes).facts
    assert f["wa_about"] == "Baking since 2010" and f["wa_business_category"] == "Bakery"


def test_checks_are_budgeted_and_only_in_waking_hours():
    store, clock = Store(), Clock(at(*MON, 11))
    for i in range(30):
        store.add_lead(f"L{i}", f"+601234{i:05d}")
    relay = CheckingRelay({})
    cfg = Config(human_pacing=False, checks_per_run=8, checks_per_day=12)
    swarm = Swarm(store, relay, cfg, None, clock)
    for _ in range(5):
        swarm.tick()
    assert sum(map(len, relay.asked)) == 12
    clock.t = at(*MON, 23)
    store2_asked = len(relay.asked)
    swarm.tick()
    assert len(relay.asked) == store2_asked


def test_research_never_overwrites_the_owners_own_notes():
    store = Store()
    lid = store.add_lead("A", "+60111111111", "", {"wa_about": "mine"})
    store.add_facts(lid, {"wa_about": "theirs", "wa_website": "x.my"})
    assert store.lead(lid).facts == {"wa_about": "mine", "wa_website": "x.my"}
