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
from atlas.models import Stage
from atlas.store import Store
from atlas.swarm import Swarm
from atlas.web import PAGE, Control, make_handler
from test_relay import FakeRelay
from test_swarm import Clock, FakeChannel
from atlas.channels.relay import RelayChannel


class DeadRelay:
    def status(self): raise ConnectionRefusedError("nothing on 3001")
    def qr(self): raise ConnectionRefusedError()


class PairingRelay:
    def status(self): return {"connected": False, "connecting": True}
    def qr(self): return "data:image/png;base64,AAAA"


def control(relay=None, cfg=None):
    store, clock = Store(), Clock()
    cfg = cfg or Config(human_pacing=False)
    return Control(Swarm(store, FakeChannel(), cfg, now=clock), relay or PairingRelay(), every=0.05)


def test_relay_not_running_is_a_state_not_a_crash():
    st = control(DeadRelay()).state()
    assert st["relay"]["running"] is False and "ConnectionRefused" in st["relay"]["error"]


def test_qr_is_offered_while_unpaired():
    assert control().state()["relay"]["qr"].startswith("data:image/png")


def test_import_then_run_sends_and_shows_in_activity():
    c = control()
    r = c.import_csv("Name , Phone ,company\nAisha,012-3456789,Kedai\nBad,xx,\n")
    assert r["done"] == 1 and r["skipped"] == {"bad_phone": 1}
    c.run_once()
    st = c.state()
    assert st["stages"] == {"contacted": 1}
    assert st["activity"][0]["dir"] == "out" and "STOP" in st["activity"][0]["text"]


def test_auto_run_is_off_on_start_and_only_runs_when_switched_on():
    c = control()
    c.import_csv("name,phone\nAisha,0123456789\n")
    t = threading.Thread(target=c.loop, daemon=True)
    t.start()
    threading.Event().wait(0.2)
    assert c.ctx.store.lead(1).stage is Stage.NEW
    c.auto = True
    threading.Event().wait(0.3)
    c._stop.set()
    assert c.ctx.store.lead(1).stage is Stage.CONTACTED


def test_a_run_already_in_progress_is_not_doubled():
    c = control()
    c._run_lock.acquire()
    assert c.run_once() == {"busy": True}


@pytest.fixture
def server():
    c = control()
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
    assert _post(url + "/api/import", {"csv": "name,phone\nA,0123456789"})["done"] == 1
    assert _post(url + "/api/auto", {"on": True}) == {"auto": True}
    st = json.loads(urllib.request.urlopen(url + "/api/state").read())
    assert st["auto"] is True and st["stages"] == {"new": 1}
    assert b"<title>COAI Atlas</title>" in urllib.request.urlopen(url + "/").read()


def test_serve_binds_loopback_only():
    src = (Path(__file__).parent.parent / "src/atlas/web.py").read_text()
    assert 'ThreadingHTTPServer(("127.0.0.1", port)' in src


def _script():
    return re.search(r"<script>(.*)</script>", PAGE, re.S).group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_page_script_runs_and_renders_every_relay_state(tmp_path):
    """Parsing is not running: execute the page against a stub DOM and read the HTML it writes."""
    harness = r"""
const els = {};
const el = (id) => els[id] || (els[id] = {id, innerHTML:"", textContent:"", value:"", checked:false, disabled:false});
global.document = {getElementById: el};
global.setInterval = () => 0;
let states = [];
// Shift at call time: the page calls refresh() once on load and takes the first state.
global.fetch = async () => { const s = states.shift(); return {json: async () => s}; };
const plan = {date:"2026-10-05", working:true, start:1791117000, end:1791148000, lunch_start:1791125000, lunch_end:1791130400, quota:14};
const base = {stages:{new:2,contacted:1}, activity:[{dir:"in",at:1,text:"<b>stop</b>",name:"A",phone:"+60",intent:"opt_out",source:""}],
  queue:[{id:1,kind:"followup_1",text:"Hi <Aisha>",source:"ai",name:"Aisha",company:"Kedai",phone:"+60"}],
  pacing:{enabled:true, plan, ready:false, why:"waiting", next_at:1791120000, now:1791118000},
  writer:"Claude (test-model)", auto:false, last_run:null, cap:20};
states = [
  {...base, relay:{running:false}},   // consumed by the load-time refresh
  {...base, relay:{running:false}},
  {...base, relay:{running:true, connected:false, qr:"data:image/png;base64,QQ"}},
  {...base, relay:{running:true, connected:true, daily_sent:3, daily_cap:30}, last_run:{at:1, reports:[{agent:"sender",done:1,skipped:{quiet_hours:2}}]},
   writer:"", queue:[], pacing:{enabled:true, plan:{...plan, working:false}, ready:false, why:"day_off", next_at:null, now:1}},
];
SCRIPT
(async () => {
  await new Promise((r) => setTimeout(r, 0));
  const out = [];
  for (let i = 0; i < 3; i++) { await refresh(); out.push(el("relay").innerHTML); }
  out.push(el("stages").innerHTML, el("feed").innerHTML, el("last").innerHTML,
           el("pace").innerHTML, el("writer").textContent, el("queue").innerHTML);
  console.log(JSON.stringify(out));
})();
""".replace("SCRIPT", _script())
    f = tmp_path / "h.js"
    f.write_text(harness)
    r = subprocess.run(["node", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    dead, pairing, live, stages, feed, last, pace, writer, queue = json.loads(r.stdout)
    assert "Relay not running" in dead
    assert 'src="data:image/png;base64,QQ"' in pairing and "Linked devices" in pairing
    assert "Connected" in live and "3 / 30" in live
    assert "<td>New</td><td>2</td>" in stages and "<td>Won</td><td>0</td>" in stages
    assert "&lt;b&gt;stop&lt;/b&gt;" in feed               # a lead's text is escaped
    assert "quiet_hours 2" in last
    # last render was the day-off, template-writer, empty-queue state
    assert "Day off" in pace
    assert "templates" in writer and "ANTHROPIC_API_KEY" in writer
    assert "Nothing waiting" in queue
