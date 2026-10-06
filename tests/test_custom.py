"""The owner's own templates, the video library, and the settings card."""
import urllib.parse
import json
import random
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from atlas import settings, templates
from atlas.agents import Ctx
from atlas.agents.writer import Writer
from atlas.config import Config
from atlas.media import Library, safe_name
from atlas.models import Lead, Stage
from atlas.store import Store
from atlas.swarm import Swarm
from atlas.web import Control, make_handler
from test_human import FakeLLM
from test_swarm import Clock, FakeChannel

AISHA = Lead(1, "Aisha Rahman", "+60123456789", company="Kedai Aisha",
             facts={"how_we_know": "Penang Rotary", "industry": "bakery", "favourite": "kaya puffs"})


# ---- templates ---------------------------------------------------------------------

def test_spin_fill_and_versions():
    rng = random.Random(1)
    t = {"text": "{Hi|Hello} {first_name}, {owner} here from {how_we_know}. Still into {favourite}?\n---\nYo {first_name}!"}
    outs = {templates.build(t, AISHA, Config(), rng) for _ in range(40)}
    assert "Hi Aisha, Lyon here from Penang Rotary. Still into kaya puffs?" in outs
    assert "Hello Aisha, Lyon here from Penang Rotary. Still into kaya puffs?" in outs
    assert "Yo Aisha!" in outs and len(outs) == 3


def test_missing_values_leave_no_debris():
    bare = Lead(2, "", "+60111111111")
    out = templates.fill("Hi {first_name}, it's {owner} ({how_we_know}). How is {company} ?", bare, Config())
    assert out == "Hi, it's Lyon. How is?"


def test_nested_spin():
    assert templates.spin("{a|{b|c}}", random.Random(3)) in {"a", "b", "c"}


def test_close_enough_allows_light_edits_only():
    base = "Hi Aisha, Lyon here. We're running a free AI demo on 12/10, book at https://coai.my/demo"
    light = "Hey Aisha! Lyon here. We're running a free AI demo on 12/10 - book at https://coai.my/demo"
    assert templates.close_enough(base, light)
    assert not templates.close_enough(base, light.replace("https://coai.my/demo", "our site"))   # lost the link
    assert not templates.close_enough(base, light.replace("12/10", "next week"))                 # changed a date
    assert templates.NUMBER.findall("RM1,500 on 12/10, call 012-345 6789.") == ["1,500", "12/10", "012-345", "6789"]
    assert not templates.close_enough(base, "Hello! I have an amazing offer for you, interested?")  # rewritten
    assert not templates.close_enough(base, base + " Reply STOP to opt out.")                   # added own opt-out


def ctx_with(llm=None, tmp_path=None):
    store = Store()
    c = Ctx(store, Config(human_pacing=False), llm, Clock(), random.Random(5))
    c.media = Library(tmp_path or "media-none", store) if tmp_path else None
    return c


def test_writer_uses_the_owners_template_and_ai_light_edit():
    base_text = "Hi {first_name}, great to hear from you! I started COAI, we help bakeries get orders on WhatsApp. Keen for a coffee?"
    filled = "Hi Aisha, great to hear from you! I started COAI, we help bakeries get orders on WhatsApp. Keen for a coffee?"
    edited = "Hi Aisha, so good to hear from you! I started COAI, we help bakeries like Kedai Aisha get orders on WhatsApp. Keen for a coffee?"
    llm = FakeLLM(edited)
    c = ctx_with(llm)
    lid = c.store.add_lead("Aisha Rahman", "+60123456789", "Kedai Aisha")
    c.store.set_stage(lid, Stage.CONFIRMED)
    templates.save(c.store, "pitch", base_text, ai=True, media=False)
    Writer().run(c)
    (_, d), = c.store.pending_drafts()
    assert d.source == "yours+ai" and d.text.startswith(edited)
    assert d.text.endswith(c.cfg.opt_out_line)                       # pitch sells: opt-out added
    assert filled in llm.calls[0][1]                                 # the AI saw the filled template


def test_an_ai_edit_that_strays_is_dropped_for_the_owners_words():
    c = ctx_with(FakeLLM("Totally different message about something else entirely."))
    lid = c.store.add_lead("Aisha", "+60123456789")
    templates.save(c.store, "opener", "Hi {first_name}, is this still your number? {owner} here.", ai=True, media=False)
    rep = Writer().run(c)
    (_, d), = c.store.pending_drafts()
    assert d.text == "Hi Aisha, is this still your number? Lyon here." and d.source == "yours"
    assert rep.skipped["ai_edit_too_different"] == 1                # opener: no opt-out line


def test_owner_written_opt_out_is_not_doubled():
    c = ctx_with()
    lid = c.store.add_lead("Aisha", "+60123456789")
    c.store.set_stage(lid, Stage.CONFIRMED)
    templates.save(c.store, "pitch", "Hi {first_name}, COAI is live. Reply STOP to stop.", ai=False, media=False)
    Writer().run(c)
    assert c.store.pending_drafts()[0][1].text.lower().count("stop") == 2   # theirs only, not ours too


def test_empty_template_returns_to_atlas_wording():
    c = ctx_with()
    templates.save(c.store, "opener", "Hi {first_name}", False, False)
    templates.save(c.store, "opener", "   ", False, False)
    assert templates.load(c.store) == {}


# ---- videos ----------------------------------------------------------------------

def test_random_video_never_repeats_for_the_same_person_until_all_used(tmp_path):
    c = ctx_with(tmp_path=tmp_path)
    for n in ("a.mp4", "b.mp4", "c.png"):
        c.media.save(n, b"x")
    c.media.set_on("c.png", False)                                   # unticked: never picked
    lid = c.store.add_lead("Aisha", "+60123456789")
    picks = []
    for _ in range(2):
        m = c.media.pick(lid, c.rng)
        c.store.mark_sent(lid, Stage.CONTACTED, 1.0, "t", "", m)
        picks.append(m)
    assert sorted(picks) == ["a.mp4", "b.mp4"]
    assert c.media.pick(lid, c.rng) in {"a.mp4", "b.mp4"}             # all used: any ticked one again


def test_attach_flag_puts_a_video_on_the_draft_and_the_send(tmp_path):
    store, clock = Store(), Clock()
    lib = Library(tmp_path, store)
    lib.save("intro.mp4", b"video")
    ch = FakeChannel()
    swarm = Swarm(store, ch, Config(human_pacing=False), None, clock, media=lib)
    lid = store.add_lead("Aisha", "+60123456789")
    store.set_stage(lid, Stage.CONFIRMED)
    templates.save(store, "pitch", "Hi {first_name}, have a look at this!", ai=False, media=True)
    swarm.tick()
    assert ch.media == ["intro.mp4"]
    assert store.activity()[0]["media"] == "intro.mp4"


def test_file_names_are_cleaned_and_kinds_limited(tmp_path):
    assert safe_name("..\\..\\evil/../Intro Video!.MP4") == "Intro Video.mp4"
    assert safe_name("notes.txt") is None and safe_name(".mp4") is None
    lib = Library(tmp_path, Store())
    assert lib.save("a.mp4", b"1")["name"] == "a.mp4"
    assert lib.save("a.mp4", b"2")["name"] == "a (2).mp4"               # never overwrites a queued file
    assert lib.save("x.exe", b"1")["error"]
    assert lib.delete("../../etc/passwd")["error"]


# ---- settings --------------------------------------------------------------------

def test_key_is_saved_write_only_and_other_lines_survive(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nANTHROPIC_API_KEY=old\n")
    assert settings.save_claude_key(env, "  sk-ant-api03-abcdefghijklmnop  ")["ok"]
    assert env.read_text() == "OTHER=1\nANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop\n"
    assert settings.save_claude_key(env, "hello")["error"]
    settings.remove_claude_key(env)
    assert env.read_text() == "OTHER=1\n"
    assert not env.read_bytes().startswith(b"\xef\xbb\xbf")


def test_profile_changes_the_running_config_and_blank_means_default():
    store, cfg = Store(), Config()
    settings.save_profile(cfg, store, {"owner_name": "Lyon Chiam", "opt_out_line": ""})
    assert cfg.owner_name == "Lyon Chiam" and cfg.opt_out_line == Config().opt_out_line
    cfg2 = Config()
    settings.apply_profile(cfg2, store)                               # next start
    assert cfg2.owner_name == "Lyon Chiam"


# ---- over HTTP -------------------------------------------------------------------

@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = Store()
    swarm = Swarm(store, cfg=Config(human_pacing=False), now=Clock(), channels={"1": FakeChannel()})
    c = Control(swarm, tmp_path / ".env", media_dir=tmp_path / "media")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(c))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", c, tmp_path
    srv.shutdown()


def _post(url, body, raw=False):
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"X-Atlas": "1"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_upload_toggle_delete_over_http(page):
    url, c, tmp = page
    r = _post(url + "/api/media-upload?name=" + urllib.parse.quote("My Intro.mp4"), b"\x00" * 1000, raw=True)
    assert r == {"ok": True, "name": "My Intro.mp4", "big": False}
    assert (tmp / "media" / "My Intro.mp4").read_bytes() == b"\x00" * 1000
    _post(url + "/api/media-toggle", {"name": "My Intro.mp4", "on": False})
    st = json.loads(urllib.request.urlopen(url + "/api/state").read())
    assert st["media"][0]["on"] is False
    _post(url + "/api/media-delete", {"name": "My Intro.mp4"})
    assert not (tmp / "media" / "My Intro.mp4").exists()


def test_key_never_appears_in_any_page_response(page):
    url, c, tmp = page
    r = _post(url + "/api/key", {"key": "sk-ant-api03-SECRETSECRETSECRET"})
    assert r["ok"] is True and r["test"]["ok"] is False             # saved, even though the test call fails here
    st = urllib.request.urlopen(url + "/api/state").read().decode()
    assert "SECRET" not in st and "SECRET" not in json.dumps(r)
    assert json.loads(st)["writer"]["claude_key"] is True


def test_template_save_and_preview_over_http(page):
    url, c, tmp = page
    r = _post(url + "/api/template-preview", {"text": "{Hi|Hey} {first_name}!"})
    assert len(r["samples"]) == 3 and all(s["text"] in ("Hi Aisha!", "Hey Aisha!") for s in r["samples"])
    _post(url + "/api/template", {"kind": "pitch", "text": "Hello {first_name}", "ai": True, "media": True})
    st = json.loads(urllib.request.urlopen(url + "/api/state").read())
    assert st["templates"]["pitch"] == {"text": "Hello {first_name}", "ai": True, "media": True}
