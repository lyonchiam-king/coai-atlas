"""The control page: relay status, the pairing QR, leads, and the run buttons.

Stdlib only, bound to 127.0.0.1. It can send messages to real people, so every
POST needs the X-Atlas header: a page on another site can make a browser POST
a form to localhost, but it cannot add a custom header without a CORS
preflight, which this server never answers.
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agents import Ctx
from .agents.intake import Intake
from .models import Report
from .swarm import Swarm

AUTO_EVERY = 300   # seconds between automatic runs; the guard still applies to each


class Control:
    """Everything the page can do, kept apart from HTTP so it can be tested directly."""

    def __init__(self, swarm: Swarm, relay, every: float = AUTO_EVERY):
        self.swarm, self.relay, self.every = swarm, relay, every
        self.auto = False          # off on every start: sending is never a surprise
        self.last_run: dict | None = None
        self._run_lock = threading.Lock()
        self._stop = threading.Event()

    @property
    def ctx(self) -> Ctx:
        return self.swarm.ctx

    def relay_state(self) -> dict:
        try:
            st = self.relay.status()
        except Exception as e:     # not running is a state to show, not an error to raise
            return {"running": False, "error": f"{type(e).__name__}: {e}"}
        qr = None
        if not st.get("connected"):
            try:
                qr = self.relay.qr()
            except Exception:
                qr = None
        return {"running": True, **st, "qr": qr}

    def state(self) -> dict:
        return {
            "relay": self.relay_state(),
            "stages": self.ctx.store.stage_counts(),
            "activity": self.ctx.store.activity(),
            "auto": self.auto,
            "every": self.every,
            "last_run": self.last_run,
            "cap": self.ctx.cfg.daily_cap,
        }

    def run_once(self) -> dict:
        # One tick at a time: a button press during an automatic run must not
        # draft and send the same follow-up twice.
        if not self._run_lock.acquire(blocking=False):
            return {"busy": True}
        try:
            reports = self.swarm.tick()
            self.last_run = {"at": time.time(), "reports": [_report(r) for r in reports]}
            return self.last_run
        finally:
            self._run_lock.release()

    def import_csv(self, text: str) -> dict:
        return _report(Intake().run_text(self.ctx, text))

    def loop(self) -> None:
        """Background thread. Catches everything: a dead loop stops all future runs silently."""
        while not self._stop.wait(self.every):
            if not self.auto:
                continue
            try:
                self.run_once()
            except Exception as e:
                self.last_run = {"at": time.time(), "error": f"{type(e).__name__}: {e}"}


def _report(r: Report) -> dict:
    return {"agent": r.agent, "done": r.done, "skipped": r.skipped}


def make_handler(control: Control):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif self.path == "/api/state":
                self._json(200, control.state())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.headers.get("X-Atlas") != "1":
                return self._json(403, {"error": "missing X-Atlas header"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "body must be JSON"})
            if self.path == "/api/run":
                self._json(200, control.run_once())
            elif self.path == "/api/auto":
                control.auto = bool(body.get("on"))
                self._json(200, {"auto": control.auto})
            elif self.path == "/api/import":
                self._json(200, control.import_csv(str(body.get("csv", ""))))
            else:
                self._json(404, {"error": "not found"})

    return Handler


def serve(control: Control, port: int = 8790, open_browser: bool = True) -> None:
    # Loopback only. There is no login, so this must never listen anywhere else.
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(control))
    threading.Thread(target=control.loop, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"Atlas control page: {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        control._stop.set()
        srv.server_close()


# A raw string, so a JS escape like \n reaches the browser as written.
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>COAI Atlas</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14171c;--mute:#5d6673;--line:#e3e6ea;--ok:#127a3e;--warn:#a15c00;--bad:#b42318;--accent:#1f5eff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--ink:#e8eaee;--mute:#9aa3ae;--line:#2a2f37;--ok:#3ccf7a;--warn:#f0a640;--bad:#ff6b5e;--accent:#6d95ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0 16px}h2{font-size:15px;margin:0 0 10px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-weight:600;font-size:13px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mute{color:var(--mute)}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
textarea{width:100%;min-height:110px;font:13px ui-monospace,monospace;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
@media (max-width:600px){textarea{font-size:16px}}
table{width:100%;border-collapse:collapse}td{padding:4px 0;border-bottom:1px solid var(--line)}td:last-child{text-align:right;font-weight:600}
.feed{max-height:380px;overflow:auto}.msg{padding:8px 0;border-bottom:1px solid var(--line)}.msg p{margin:2px 0 0;white-space:pre-wrap;word-break:break-word}
.qr img{width:100%;max-width:260px;background:#fff;padding:6px;border-radius:8px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
</style></head><body><main>
<h1>COAI Atlas</h1>
<div class="grid">
  <section class="card"><h2>WhatsApp</h2><div id="relay" class="mute">Checking…</div></section>
  <section class="card"><h2>Run</h2>
    <div class="row"><button class="primary" id="run">Run now</button>
    <label class="row"><input type="checkbox" id="auto"> Auto-run every <span id="every"></span> min</label></div>
    <p class="mute" id="rules"></p><div id="last" class="mute"></div></section>
  <section class="card"><h2>Pipeline</h2><table id="stages"></table></section>
  <section class="card"><h2>Add leads</h2>
    <p class="mute">Paste CSV with a header row: <code>name,phone,company</code>. Malaysian numbers can be local (012-345 6789).</p>
    <textarea id="csv" placeholder="name,phone,company&#10;Aisha,012-3456789,Kedai Aisha"></textarea>
    <div class="row"><button id="import">Import</button><span id="imported" class="mute"></span></div></section>
</div>
<section class="card" style="margin-top:14px"><h2>Activity</h2><div class="feed" id="feed"></div></section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const when = (t) => new Date(t * 1000).toLocaleString();
const ORDER = ["new","contacted","followup_1","followup_2","replied","won","lost","do_not_contact"];
const LABEL = {new:"New",contacted:"Contacted",followup_1:"Follow-up 1 sent",followup_2:"Follow-up 2 sent",replied:"Replied — your turn",won:"Won",lost:"Lost",do_not_contact:"Do not contact"};
let busy = false;

async function post(path, body) {
  const r = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json","X-Atlas":"1"}, body:JSON.stringify(body || {})});
  return r.json();
}

function relayHtml(r) {
  if (!r.running) return '<span class="pill bad">Relay not running</span><p class="mute">Start it with Atlas.bat. The relay window must stay open.</p>';
  if (r.connected) return '<span class="pill ok">Connected</span><p class="mute">Sent today: ' + esc(r.daily_sent) + ' / ' + esc(r.daily_cap) + '</p>';
  let h = r.qr ? '<span class="pill warn">Scan to link</span>' : (r.reconnecting ? '<span class="pill warn">Reconnecting</span>' : '<span class="pill warn">Connecting</span>');
  if (r.qr) h += '<p class="mute">On your phone: WhatsApp → Settings → Linked devices → Link a device.</p><div class="qr"><img alt="WhatsApp pairing QR" src="' + esc(r.qr) + '"></div>';
  if (!r.qr && !r.last_error) h += '<p class="mute">The QR appears here in a few seconds. If this stays for over a minute, check the internet connection, then relay-log.txt in the Atlas folder.</p>';
  if (r.last_error) h += '<p class="bad">' + esc(r.last_error) + '</p>';
  return h;
}

function lastHtml(l) {
  if (!l) return "Not run yet.";
  if (l.error) return '<span class="bad">Last run failed: ' + esc(l.error) + '</span>';
  return "Last run " + esc(when(l.at)) + ": " + l.reports.map((r) => esc(r.agent) + " " + esc(r.done) + (Object.keys(r.skipped).length ? " (" + esc(Object.entries(r.skipped).map((e) => e[0] + " " + e[1]).join(", ")) + ")" : "")).join(" · ");
}

function render(s) {
  $("relay").innerHTML = relayHtml(s.relay);
  $("auto").checked = s.auto;
  $("every").textContent = Math.round(s.every / 60);
  $("rules").textContent = "Sends only 09:00–21:00 Malaysia time, at most " + s.cap + " a day. A reply or STOP pauses the lead.";
  $("last").innerHTML = lastHtml(s.last_run);
  $("stages").innerHTML = ORDER.map((k) => "<tr><td>" + LABEL[k] + "</td><td>" + (s.stages[k] || 0) + "</td></tr>").join("");
  $("feed").innerHTML = s.activity.length ? s.activity.map((a) =>
    '<div class="msg"><span class="' + (a.dir === "in" ? "ok" : "mute") + '">' + (a.dir === "in" ? "← " : "→ ") + esc(a.name) + " " + esc(a.phone) + "</span> <span class=\"mute\">" + esc(when(a.at)) + (a.intent ? " · " + esc(a.intent) : "") + "</span><p>" + esc(a.text) + "</p></div>"
  ).join("") : '<p class="mute">Nothing sent or received yet.</p>';
}

async function refresh() {
  if (busy) return;
  try { render(await (await fetch("/api/state")).json()); }
  catch (e) { $("relay").innerHTML = '<span class="pill bad">Atlas is not running</span>'; }
}

$("run").onclick = async () => {
  busy = true; $("run").disabled = true; $("run").textContent = "Running…";
  try { const r = await post("/api/run"); if (r.busy) $("last").textContent = "Already running — try again in a moment."; }
  finally { busy = false; $("run").disabled = false; $("run").textContent = "Run now"; refresh(); }
};
$("auto").onchange = async (e) => { await post("/api/auto", {on: e.target.checked}); refresh(); };
$("import").onclick = async () => {
  const r = await post("/api/import", {csv: $("csv").value});
  const sk = Object.entries(r.skipped || {}).map((e) => e[1] + " " + e[0].replace("_", " ")).join(", ");
  $("imported").textContent = "Added " + r.done + (sk ? ", skipped " + sk : "");
  if (r.done) $("csv").value = "";
  refresh();
};
refresh();
setInterval(refresh, 3000);
</script></body></html>
"""
