import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from atlas.config import Config
from atlas.models import Inbound, Stage
from atlas.store import Store
from atlas.swarm import Swarm
from atlas.web import PAGE, Control, make_handler
from test_swarm import Clock, FakeChannel


class DeadRelay(FakeChannel):
    def status(self): raise ConnectionRefusedError("WhatsApp relay is not running")
    def qr(self): raise ConnectionRefusedError()


class PairingRelay(FakeChannel):
    def status(self): return {"connected": False, "connecting": True}
    def qr(self): return "data:image/png;base64,AAAA"
    def contacts(self): return [{"phone": "+60123456789", "name": "Aisha Bakery", "notify": "Aisha", "last_chat": 1561939200}]


def control(tmp_path, relays=None, cfg=None):
    store, clock = Store(), Clock()
    cfg = cfg or Config(human_pacing=False, two_step=False)
    relays = relays or {"1": PairingRelay(), "2": DeadRelay(), "3": PairingRelay()}
    return Control(Swarm(store, cfg=cfg, now=clock, channels=relays), tmp_path / ".env", every=0.05)


def test_each_phone_reports_its_own_state(tmp_path):
    st = control(tmp_path).state()
    one, two, three = st["phones"]
    assert one["relay"]["qr"].startswith("data:image/png") and one["name"] == "Phone 1"
    assert two["relay"]["running"] is False and "not running" in two["relay"]["error"]
    assert three["id"] == "3"


def test_import_onto_a_phone_then_run_sends_from_that_phone(tmp_path):
    relays = {"1": PairingRelay(), "2": PairingRelay(), "3": PairingRelay()}
    c = control(tmp_path, relays)
    r = c.import_text("Name , Phone ,company\nAisha,012-3456789,Kedai\nBad,xx,\n", "2", "Rotary")
    assert r["done"] == 1 and r["skipped"] == {"bad_phone": 1}
    c.run_once()
    assert relays["2"].sent and not relays["1"].sent and not relays["3"].sent
    st = c.state()
    assert st["phones"][1]["stages"] == {"contacted": 1}
    assert st["lists"] == [{"account": "2", "list_name": "Rotary", "n": 1}]
    assert st["activity"][0]["account"] == "2"


def test_import_from_a_phones_whatsapp_chats(tmp_path):
    c = control(tmp_path)
    r = c.import_whatsapp("1", "")
    assert r["done"] == 1
    lead = c.ctx.store.lead(1)
    assert lead.list_name == "WhatsApp chats" and lead.facts["last_chatted"] == "July 2019"
    assert c.import_whatsapp("2", "")["error"]          # DeadRelay has no contacts() -> not importable


def test_rename_and_mark(tmp_path):
    c = control(tmp_path)
    assert c.rename("2", "  Office phone ")["name"] == "Office phone"
    assert c.state()["phones"][1]["name"] == "Office phone"
    lid = c.ctx.store.add_lead("A", "+60123456789")
    assert c.mark(lid, "won")["error"]                    # only someone who replied
    c.ctx.store.set_stage(lid, Stage.REPLIED)
    c.ctx.store.add_inbound(Inbound(lid, "Sounds good, call me", 5.0))
    assert c.state()["replied"][0]["last_text"] == "Sounds good, call me"
    assert c.mark(lid, "won") == {"ok": True} and c.ctx.store.lead(lid).stage is Stage.WON


def test_writer_settings_persist_and_never_expose_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = control(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-secret-value\n")
    w = c.set_writer({"writer": "templates", "claude_model": "claude-haiku-4-5", "ollama_model": "x"})
    assert w["mode"] == "templates" and w["active"] == "" and c.ctx.llm is None
    assert w["claude_key"] is True and "sk-secret" not in json.dumps(c.state())
    assert c.set_writer({"claude_model": "made-up-model"})["claude_model"] == "claude-haiku-4-5"


def test_auto_run_is_off_on_start_and_only_runs_when_switched_on(tmp_path):
    c = control(tmp_path)
    c.import_text("name,phone\nAisha,0123456789\n", "1")
    t = threading.Thread(target=c.loop, daemon=True)
    t.start()
    threading.Event().wait(0.2)
    assert c.ctx.store.lead(1).stage is Stage.NEW
    c.set_auto(True)
    threading.Event().wait(0.3)
    c._stop.set()
    assert c.ctx.store.lead(1).stage is Stage.CONTACTED


def test_a_run_already_in_progress_is_not_doubled(tmp_path):
    c = control(tmp_path)
    c._run_lock.acquire()
    assert c.run_once() == {"busy": True}


@pytest.fixture
def server(tmp_path):
    c = control(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(c))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", c
    srv.shutdown()


def _post(url, body, header=True):
    h = {"Content-Type": "application/json", **({"X-Atlas": "1"} if header else {})}
    with urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), h)) as r:
        return json.loads(r.read())


def test_post_without_the_header_is_refused(server):
    url, c = server
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url + "/api/auto", {"on": True}, header=False)
    assert e.value.code == 403 and c.auto is False


def test_http_round_trip(server):
    url, c = server
    assert _post(url + "/api/import", {"text": "name,phone\nA,0123456789", "phone": "3", "list": "L"})["done"] == 1
    assert _post(url + "/api/auto", {"on": True}) == {"auto": True}
    st = json.loads(urllib.request.urlopen(url + "/api/state").read())
    assert st["auto"] is True and st["phones"][2]["stages"] == {"new": 1}
    assert b"<title>COAI Atlas</title>" in urllib.request.urlopen(url + "/").read()


def test_every_endpoint_the_page_calls_exists(server):
    url, c = server
    called = set(re.findall(r'post\("(/api/[a-z-]+)"', PAGE)) | set(re.findall(r'fetch\("(/api/[a-z-]+)"', PAGE))
    assert called >= {"/api/run", "/api/auto", "/api/import", "/api/import-whatsapp", "/api/writer",
                      "/api/rename", "/api/mark", "/api/state"}
    for path in called - {"/api/state", "/api/run"}:
        try:
            _post(url + path, {})
        except urllib.error.HTTPError as e:
            assert e.code != 404, path


def test_a_bad_request_gets_an_error_not_a_dropped_connection(server):
    url, c = server
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url + "/api/mark", {"lead": 999, "outcome": "won"})
    assert e.value.code == 400 and "error" in json.loads(e.value.read())


def test_serve_binds_loopback_only():
    src = (Path(__file__).parent.parent / "src/atlas/web.py").read_text()
    assert 'ThreadingHTTPServer(("127.0.0.1", port)' in src


def test_no_two_page_functions_share_a_name():
    names = re.findall(r"^(?:async )?function (\w+)", _script(), re.M)
    assert len(names) == len(set(names))


def _script():
    return re.search(r"<script>(.*)</script>", PAGE, re.S).group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_page_script_runs_and_renders_every_relay_state(tmp_path):
    """Parsing is not running: execute the page against a stub DOM and read the HTML it writes."""
    harness = r"""
const els = {};
const el = (id) => els[id] || (els[id] = {id, innerHTML:"", textContent:"", value:"", checked:false, disabled:false, dataset:{}, files:null});
global.document = {getElementById: el, activeElement: null, addEventListener: () => 0};
global.window = {prompt: () => null};
global.setInterval = () => 0;
let states = [];
// Shift at call time: the page calls refresh() once on load and takes the first state.
global.fetch = async () => { const s = states.shift(); return {json: async () => s}; };
const plan = {date:"2026-10-05", working:true, start:1791165000, end:1791195960, lunch_start:1791174600, lunch_end:1791180000, quota:14};
const pace = {enabled:true, plan, ready:false, why:"waiting", next_at:1791120000, sent_today:3};
const phone = (id, relay, extra) => ({id, name:"Phone " + id, relay, pacing:{...pace, ...(extra || {})}, stages:{new:5, replied:1, confirmed:1}});
const writer = {mode:"auto", claude_model:"claude-opus-5-5", claude_models:{"claude-opus-5-5":"Opus"}, ollama_model:"", ollama_models:[], claude_key:false, claude_sdk:true, active:""};
const base = {stages:{new:2,opener_sent:1}, lists:[{account:"2", list_name:"Rotary", n:3}],
  activity:[{dir:"in",at:1,text:"<b>stop</b>",name:"A",phone:"+60",intent:"opt_out",source:"",account:"2"}],
  queue:[{id:1,kind:"opener",text:"Hi <Aisha>, is this still you?",source:"ai",name:"Aisha",company:"Kedai",phone:"+60",account:"1"}],
  replied:[{id:7,name:"Tan",phone:"+6016",account:"3",last_text:"Sounds good <3"}],
  writer, auto:false, last_run:null, cap:25, tz:"Asia/Kuala_Lumpur",
  profile:{owner_name:"Lyon", sender_name:"Lyon from COAI", offer:"AI tools", opt_out_line:"Reply STOP"},
  templates:{pitch:{text:"Hi {first_name}! <b>", ai:true, media:true}},
  media:[{name:"intro <1>.mp4", kind:"video", size:20971520, on:true, big:true}, {name:"flyer.png", kind:"image", size:1048576, on:false, big:false}]};
const fixturePhones = [phone("1", {running:false}), phone("2", {running:true, connected:false, qr:"data:image/png;base64,QQ"}),
                phone("3", {running:true, connected:true}, {plan:{...plan, working:false}, why:"day_off"})];
states = [{...base, phones: fixturePhones}, {...base, phones: fixturePhones,
  last_run:{at:1, reports:[{agent:"sender 1",done:1,skipped:{quiet_hours:2}}, {agent:"writer",done:0,skipped:{}}]}}];
SCRIPT
(async () => {
  await new Promise((r) => setTimeout(r, 0));
  await refresh();
  console.log(JSON.stringify({phones: el("phones").innerHTML, stages: el("stages").innerHTML, feed: el("feed").innerHTML,
    last: el("last").innerHTML, queue: el("queue").innerHTML, replied: el("replied").innerHTML,
    writer: el("w-status").innerHTML, lists: el("lists").innerHTML, imp: el("imp-phone").innerHTML,
    w_ollama_active: writerHtml({...writer, active:"Ollama (llama3.1:latest)"}),
    w_claude_nokey: writerHtml({...writer, mode:"claude"}),
    w_ollama_none: writerHtml({...writer, mode:"ollama"}),
    media: el("m-list").innerHTML, owner: el("p-owner_name").value, tstate: el("t-state").textContent,
    pitch: (showTemplate("pitch"), [el("t-text").value, el("t-ai").checked, el("t-media").checked]),
    key: el("k-out").innerHTML}));
})();
""".replace("SCRIPT", _script())
    f = tmp_path / "h.js"
    f.write_text(harness)
    r = subprocess.run(["node", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert "not running" not in o["last"], "render threw and fell into the catch"
    assert "Relay not running" in o["phones"]
    assert 'src="data:image/png;base64,QQ"' in o["phones"] and "Linked devices" in o["phones"]
    assert "Connected" in o["phones"] and "Day off" in o["phones"]
    assert "Sent today 3 of 14" in o["phones"] and "7 contacts" in o["phones"]
    # Penang time whatever zone the machine is in (this container runs UTC)
    assert re.search(r"0?9:50.{0,4}\u2013.{0,4}(18|0?6):26", o["phones"]), o["phones"]
    assert "Asked “is this still you?”</td><td>1" in o["stages"] and "<td>Won</td><td>0</td>" in o["stages"]
    assert "&lt;b&gt;stop&lt;/b&gt;" in o["feed"] and "Phone 2" in o["feed"]       # escaped, labelled by phone
    assert "quiet_hours 2" in o["last"] and "writer" not in o["last"]               # idle agents left out
    assert "is this still you?" in o["queue"] and "&lt;Aisha&gt;" in o["queue"]
    assert "Sounds good &lt;3" in o["replied"] and 'data-mark="won"' in o["replied"]
    assert "templates" in o["writer"] and "install Ollama" in o["writer"]
    # Ollama writing in Automatic mode: not a word about Claude keys
    assert "Writing with Ollama" in o["w_ollama_active"] and "ANTHROPIC" not in o["w_ollama_active"]
    assert "ANTHROPIC_API_KEY" in o["w_claude_nokey"] and "ollama pull" in o["w_ollama_none"]
    assert "Phone 2: Rotary" in o["lists"]
    assert o["imp"].count("<option") == 3

    assert "intro &lt;1&gt;.mp4" in o["media"] and "Over 16 MB" in o["media"] and "20.0 MB" in o["media"]
    assert 'data-media-toggle="flyer.png">' in o["media"]                     # unticked one shows unticked
    assert o["owner"] == "Lyon" and "own words" in o["tstate"]                # opener has no template
    assert o["pitch"] == ["Hi {first_name}! <b>", True, True]
    assert "No key saved" in o["key"] and "sk-" not in o["key"]
