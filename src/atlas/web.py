"""The control page: three phones, their lists, the writer, and the run buttons.

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
from pathlib import Path

from .agents import Ctx
from .agents.intake import Intake
from .importers import from_whatsapp
from .llm import CLAUDE_MODELS, OllamaLLM, make_writer, read_env_file
from .models import Report, Stage
from .swarm import Swarm

# How often auto-run wakes. Short on purpose: it only *checks*; the pacer decides
# whether a message actually goes, so this sets the precision of the human gaps.
AUTO_EVERY = 60
OLLAMA_URL = "http://127.0.0.1:11434"


class Control:
    """Everything the page can do, kept apart from HTTP so it can be tested directly."""

    def __init__(self, swarm: Swarm, env_file: str | Path = ".env", every: float = AUTO_EVERY):
        self.swarm, self.env_file, self.every = swarm, Path(env_file), every
        self.auto = False          # off on every start: sending is never a surprise
        self.last_run: dict | None = None
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._ollama_cache: tuple[float, list[str]] = (0.0, [])

    @property
    def ctx(self) -> Ctx:
        return self.swarm.ctx

    # ---- phones ------------------------------------------------------------

    def phone_name(self, account: str) -> str:
        saved = self.ctx.store.get(f"phone_name:{account}")
        default = next((n for a, n, _ in self.ctx.cfg.accounts if a == account), f"Phone {account}")
        return saved or default

    def rename(self, account: str, name: str) -> dict:
        name = name.strip()[:40]
        if account in self.swarm.channels and name:
            self.ctx.store.put(f"phone_name:{account}", name)
        return {"name": self.phone_name(account)}

    def relay_state(self, account: str) -> dict:
        relay = self.swarm.channels[account]
        try:
            st = relay.status()
        except Exception as e:     # not running is a state to show, not an error to raise
            return {"running": False, "error": str(e)}
        qr = None
        if not st.get("connected"):
            try:
                qr = relay.qr()
            except Exception:
                qr = None
        return {"running": True, **st, "qr": qr}

    def phone_state(self, account: str) -> dict:
        now = self.ctx.now()
        pacer = self.swarm.pacers[account]
        plan = pacer.plan(now)
        ready, why = pacer.ready(now)
        return {
            "id": account,
            "name": self.phone_name(account),
            "relay": self.relay_state(account),
            "pacing": {"enabled": self.ctx.cfg.human_pacing, "plan": plan.__dict__, "ready": ready,
                       "why": why, "next_at": pacer.next_at(), "sent_today": pacer.sent_today(now)},
            "stages": self.ctx.store.stage_counts(account),
        }

    # ---- writer ------------------------------------------------------------

    def settings(self) -> dict:
        try:
            return json.loads(self.ctx.store.get("writer_settings") or "{}")
        except ValueError:
            return {}

    def ollama_models(self, refresh: bool = False) -> list[str]:
        # The page refreshes every few seconds; asking Ollama each time would be rude.
        at, models = self._ollama_cache
        if refresh or time.time() - at > 30:
            models = OllamaLLM.list_models(self.settings().get("ollama_url") or OLLAMA_URL)
            self._ollama_cache = (time.time(), models)
        return models

    def apply_writer(self) -> None:
        self.ctx.llm = make_writer(self.settings(), self.env_file)

    def set_writer(self, body: dict) -> dict:
        s = self.settings()
        if body.get("writer") in ("auto", "claude", "ollama", "templates"):
            s["writer"] = body["writer"]
        if body.get("claude_model") in CLAUDE_MODELS:
            s["claude_model"] = body["claude_model"]
        if "ollama_model" in body:
            s["ollama_model"] = str(body["ollama_model"])[:100]
        self.ctx.store.put("writer_settings", json.dumps(s))
        self.ollama_models(refresh=True)
        self.apply_writer()
        return self.writer_state()

    def writer_state(self) -> dict:
        s = self.settings()
        try:
            import anthropic  # noqa: F401
            sdk = True
        except ImportError:
            sdk = False
        import os
        key = bool(os.environ.get("ANTHROPIC_API_KEY") or read_env_file(self.env_file).get("ANTHROPIC_API_KEY"))
        llm = self.ctx.llm
        return {
            "mode": s.get("writer", "auto"),
            "claude_model": s.get("claude_model", "claude-opus-5-5"),
            "claude_models": CLAUDE_MODELS,
            "ollama_model": s.get("ollama_model", ""),
            "ollama_models": self.ollama_models(),
            "claude_key": key,          # presence only; the key itself never leaves .env
            "claude_sdk": sdk,
            "active": llm.name if llm else "",
        }

    # ---- page --------------------------------------------------------------

    def state(self) -> dict:
        return {
            "phones": [self.phone_state(a) for a in self.swarm.channels],
            "stages": self.ctx.store.stage_counts(),
            "lists": self.ctx.store.lists(),
            "activity": self.ctx.store.activity(),
            "queue": self.ctx.store.queue_view(),
            "replied": self.ctx.store.replied_view(),
            "writer": self.writer_state(),
            "auto": self.auto,
            "last_run": self.last_run,
            "cap": self.ctx.cfg.daily_cap,
            "tz": self.ctx.cfg.tz_name,
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

    def import_text(self, text: str, account: str = "1", list_name: str = "") -> dict:
        if account not in self.swarm.channels:
            return {"error": "unknown phone"}
        return _report(Intake().run_text(self.ctx, text, account, list_name.strip()[:60]))

    def import_whatsapp(self, account: str, list_name: str = "") -> dict:
        relay = self.swarm.channels.get(account)
        if relay is None or not hasattr(relay, "contacts"):
            return {"error": "unknown phone"}
        try:
            rows = from_whatsapp(relay.contacts())
        except Exception as e:
            return {"error": f"Could not read this phone's WhatsApp contacts: {e}"}
        if not rows:
            return {"error": "No named WhatsApp contacts yet. Link the phone first; contacts arrive "
                             "a few minutes after linking."}
        return _report(Intake().run_rows(self.ctx, rows, account, (list_name or "WhatsApp chats").strip()[:60]))

    def mark(self, lead_id: int, outcome: str) -> dict:
        """The owner's verdict on someone who replied. Only from the "your turn" list."""
        stage = {"won": Stage.WON, "lost": Stage.LOST, "stop": Stage.DO_NOT_CONTACT}.get(outcome)
        if stage is None:
            return {"error": "unknown outcome"}
        if self.ctx.store.lead(lead_id).stage is not Stage.REPLIED:
            return {"error": "only a lead waiting on you can be marked"}
        self.ctx.store.set_stage(lead_id, stage)
        return {"ok": True}

    def set_auto(self, on: bool) -> dict:
        self.auto = on
        return {"auto": self.auto}

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
            a = str(body.get("phone", "1"))
            routes = {
                "/api/run": lambda: control.run_once(),
                "/api/auto": lambda: control.set_auto(bool(body.get("on"))),
                "/api/import": lambda: control.import_text(str(body.get("text", "")), a, str(body.get("list", ""))),
                "/api/import-whatsapp": lambda: control.import_whatsapp(a, str(body.get("list", ""))),
                "/api/writer": lambda: control.set_writer(body),
                "/api/rename": lambda: control.rename(a, str(body.get("name", ""))),
                "/api/mark": lambda: control.mark(int(body.get("lead", 0)), str(body.get("outcome", ""))),
            }
            if self.path not in routes:
                return self._json(404, {"error": "not found"})
            try:
                self._json(200, routes[self.path]())
            except Exception as e:   # a bad request is an answer on the page, not a dropped connection
                self._json(400, {"error": f"{type(e).__name__}: {e}"})

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
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0}h2{font-size:15px;margin:0 0 10px}h3{font-size:14px;margin:0 0 6px}
.top{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}.top .grow{flex:1}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-weight:600;font-size:13px;background:var(--bg)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mute{color:var(--mute)}.small{font-size:13px}
select,input[type=text]{max-width:100%}
button,select,input[type=text]{font:inherit;padding:7px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
button{cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.link{border:0;padding:0;background:none;color:var(--accent)}
textarea{width:100%;min-height:90px;font:13px ui-monospace,monospace;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
@media (max-width:600px){textarea,input[type=text],select{font-size:16px}}
table{width:100%;border-collapse:collapse}td{padding:3px 0;border-bottom:1px solid var(--line)}td:last-child{text-align:right;font-weight:600}
.feed{max-height:380px;overflow:auto}.msg{padding:8px 0;border-bottom:1px solid var(--line)}.msg p{margin:2px 0 0;white-space:pre-wrap;word-break:break-word}
.qr img{width:100%;max-width:220px;background:#fff;padding:6px;border-radius:8px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
label.blk{display:block;margin-top:8px}
</style></head><body><main>
<div class="top"><h1>COAI Atlas</h1><span class="grow"></span>
  <button class="primary" id="run">Run now</button>
  <label class="row" style="margin:0"><input type="checkbox" id="auto"> Auto-run</label></div>
<p class="mute small" id="last"></p>

<div class="grid" id="phones"></div>

<div class="grid">
  <section class="card"><h2>Your turn</h2><p class="mute small">They replied. Atlas has stopped; you take it from here on your phone.</p><div class="feed" id="replied"></div></section>
  <section class="card"><h2>Next to send</h2><div class="feed" id="queue"></div></section>
</div>

<div class="grid">
  <section class="card"><h2>Add contacts</h2>
    <label class="blk">Phone <select id="imp-phone"></select></label>
    <label class="blk">List name <input type="text" id="imp-list" placeholder="e.g. Rotary friends"></label>
    <label class="blk">File (.csv, .vcf) <input type="file" id="imp-file" accept=".csv,.vcf,.txt"></label>
    <p class="mute small">Or paste. Google Contacts and phone exports work as they are. Extra columns help the writer: <code>how_we_know</code>, <code>notes</code> (private), <code>hook</code>, <code>language</code> (en / ms / zh).</p>
    <textarea id="imp-text" placeholder="name,phone,company,how_we_know&#10;Aisha,012-3456789,Kedai Aisha,Penang Rotary"></textarea>
    <div class="row"><button id="imp-go">Import</button><button id="imp-wa">Import this phone's WhatsApp chats</button></div>
    <p id="imp-out" class="small"></p></section>
  <section class="card"><h2>Writer</h2>
    <label class="blk">Who writes the messages <select id="w-mode">
      <option value="auto">Automatic (Claude if a key is set, else Ollama)</option>
      <option value="claude">Claude</option><option value="ollama">Ollama (free, this PC)</option>
      <option value="templates">Templates only</option></select></label>
    <label class="blk">Claude model <select id="w-claude"></select></label>
    <label class="blk">Ollama model <select id="w-ollama"></select></label>
    <p id="w-status" class="small"></p></section>
  <section class="card"><h2>All phones</h2><table id="stages"></table><div id="lists" class="small mute"></div></section>
</div>

<section class="card"><h2>Activity</h2><div class="feed" id="feed"></div></section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
// Campaign time, not the browser's: a schedule read in the wrong zone is the wrong schedule.
let TZ = "Asia/Kuala_Lumpur";
const when = (t) => new Date(t * 1000).toLocaleString([], {timeZone: TZ});
const hm = (t) => new Date(t * 1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", timeZone: TZ});
const ORDER = ["new","opener_sent","opener_nudged","confirmed","contacted","followup_1","followup_2","replied","won","lost","wrong_number","not_on_whatsapp","do_not_contact"];
const LABEL = {new:"Not contacted yet",opener_sent:"Asked “is this still you?”",opener_nudged:"Nudged, no answer",confirmed:"Confirmed — reply going out",contacted:"Pitched",followup_1:"Follow-up 1 sent",followup_2:"Follow-up 2 sent",replied:"Replied — your turn",won:"Won",lost:"Lost",wrong_number:"Wrong number now",not_on_whatsapp:"Not on WhatsApp",do_not_contact:"Asked to stop"};
const KIND = {opener:"is this still you?",opener_nudge:"nudge",pitch:"reply + pitch",first:"first message",followup_1:"follow-up 1",followup_2:"follow-up 2"};
const WHY = {day_off:"Day off today.", before_start:"Not started yet today.", lunch:"Lunch break.", after_end:"Done for today.", quota_reached:"Today's quota is reached.", waiting:"Pausing between messages."};
let busy = false, phones = [];

async function post(path, body) {
  const r = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json","X-Atlas":"1"}, body:JSON.stringify(body || {})});
  return r.json();
}
const phoneName = (id) => (phones.find((p) => p.id === id) || {name: "Phone " + id}).name;

function relayHtml(r, id) {
  if (!r.running) return '<span class="pill bad">Relay not running</span><p class="mute small">Start Atlas with Atlas.bat. The relay windows must stay open.</p>';
  if (r.connected) return '<span class="pill ok">Connected</span>';
  let h = r.qr ? '<span class="pill warn">Scan to link</span>' : (r.reconnecting ? '<span class="pill warn">Reconnecting</span>' : '<span class="pill warn">Connecting</span>');
  if (r.qr) h += '<p class="mute small">On this phone: WhatsApp → Settings → Linked devices → Link a device.</p><div class="qr"><img alt="WhatsApp pairing QR" src="' + esc(r.qr) + '"></div>';
  if (!r.qr && !r.last_error) h += '<p class="mute small">The QR appears here in a few seconds. If it stays like this, check the internet, then relay-log-' + esc(id) + '.txt in the Atlas folder.</p>';
  if (r.last_error) h += '<p class="bad small">' + esc(r.last_error) + '</p>';
  return h;
}

function paceHtml(p) {
  if (!p.enabled) return '<span class="warn">Human pacing is off.</span>';
  const d = p.plan;
  if (!d.working) return '<span class="mute">' + WHY.day_off + '</span>';
  let h = "Sent today " + p.sent_today + " of " + d.quota + " · " + hm(d.start) + "–" + hm(d.end) + ", lunch " + hm(d.lunch_start) + "–" + hm(d.lunch_end) + ".";
  if (p.ready) h += ' <span class="ok">Ready for the next one.</span>';
  else if (p.why === "waiting" && p.next_at) h += ' <span class="mute">Next around ' + hm(p.next_at) + ".</span>";
  else h += ' <span class="mute">' + esc(WHY[p.why] || p.why) + "</span>";
  return h;
}

function phoneHtml(p) {
  const s = p.stages, total = Object.values(s).reduce((a, b) => a + b, 0);
  const waiting = (s.new || 0), talking = (s.replied || 0) + (s.confirmed || 0);
  return '<section class="card"><div class="row" style="margin:0;justify-content:space-between"><h2 style="margin:0">' + esc(p.name) + '</h2><button class="link small" data-rename="' + esc(p.id) + '">Rename</button></div>' +
    relayHtml(p.relay, p.id) +
    '<p class="small">' + paceHtml(p.pacing) + '</p>' +
    '<p class="small mute">' + total + " contacts · " + waiting + " not yet contacted · " + talking + " talking</p></section>";
}

function lastHtml(l) {
  if (!l) return "Not run yet.";
  if (l.error) return '<span class="bad">Last run failed: ' + esc(l.error) + '</span>';
  const parts = l.reports.filter((r) => r.done || Object.keys(r.skipped).length).map((r) => esc(r.agent) + " " + esc(r.done) + (Object.keys(r.skipped).length ? " (" + esc(Object.entries(r.skipped).map((e) => e[0] + " " + e[1]).join(", ")) + ")" : ""));
  return "Last run " + esc(when(l.at)) + ": " + (parts.join(" · ") || "nothing to do");
}

function writerHtml(w) {
  let h = w.active ? '<span class="ok">Writing with ' + esc(w.active) + ".</span>" : '<span class="warn">Writing from templates.</span>';
  if ((w.mode === "claude" || w.mode === "auto") && !w.claude_key) h += " Claude needs ANTHROPIC_API_KEY in the .env file.";
  if ((w.mode === "claude" || w.mode === "auto") && w.claude_key && !w.claude_sdk) h += " The Claude library is missing; run Atlas.bat again.";
  if ((w.mode === "ollama" || w.mode === "auto") && !w.ollama_models.length) h += " Ollama is not running, or has no models. Install it from ollama.com, then run: ollama pull llama3.1";
  return h;
}

function fillSelect(el, items, chosen) {
  if (el === document.activeElement) return;   // don't fight the person using it
  el.innerHTML = items.map((i) => '<option value="' + esc(i[0]) + '"' + (i[0] === chosen ? " selected" : "") + ">" + esc(i[1]) + "</option>").join("");
}

function render(s) {
  if (s.tz) TZ = s.tz;
  phones = s.phones;
  $("phones").innerHTML = s.phones.map(phoneHtml).join("");
  $("auto").checked = s.auto;
  $("last").innerHTML = lastHtml(s.last_run);
  fillSelect($("imp-phone"), s.phones.map((p) => [p.id, p.name]), $("imp-phone").value || "1");
  const w = s.writer;
  if ($("w-mode") !== document.activeElement) $("w-mode").value = w.mode;
  fillSelect($("w-claude"), Object.entries(w.claude_models), w.claude_model);
  fillSelect($("w-ollama"), [["", w.ollama_models.length ? "(choose)" : "(none installed)"]].concat(w.ollama_models.map((m) => [m, m])), w.ollama_model);
  $("w-status").innerHTML = writerHtml(w);
  $("stages").innerHTML = ORDER.map((k) => "<tr><td>" + LABEL[k] + "</td><td>" + (s.stages[k] || 0) + "</td></tr>").join("");
  $("lists").innerHTML = s.lists.length ? "<p>" + s.lists.map((l) => esc(phoneName(l.account)) + ": " + esc(l.list_name || "(no list name)") + " — " + l.n).join("<br>") + "</p>" : "";
  $("replied").innerHTML = s.replied.length ? s.replied.map((r) =>
    '<div class="msg"><b>' + esc(r.name) + "</b> " + '<span class="mute small">' + esc(r.phone) + " · " + esc(phoneName(r.account)) + "</span><p>" + esc(r.last_text) + '</p><div class="row"><button data-mark="won" data-lead="' + r.id + '">Won</button><button data-mark="lost" data-lead="' + r.id + '">Not now</button><button data-mark="stop" data-lead="' + r.id + '">Don’t contact</button></div></div>'
  ).join("") : '<p class="mute">Nobody waiting on you.</p>';
  $("queue").innerHTML = s.queue.length ? s.queue.map((q) =>
    '<div class="msg"><span>' + esc(q.name) + (q.company ? " · " + esc(q.company) : "") + '</span> <span class="mute small">' + esc(phoneName(q.account)) + " · " + esc(KIND[q.kind] || q.kind) + " · " + (q.source === "ai" ? "AI" : "template") + "</span><p>" + esc(q.text) + "</p></div>"
  ).join("") : '<p class="mute">Nothing waiting. Add contacts, then Run.</p>';
  $("feed").innerHTML = s.activity.length ? s.activity.map((a) =>
    '<div class="msg"><span class="' + (a.dir === "in" ? "ok" : "mute") + '">' + (a.dir === "in" ? "← " : "→ ") + esc(a.name) + " " + esc(a.phone) + '</span> <span class="mute small">' + esc(phoneName(a.account)) + " · " + esc(when(a.at)) + (a.intent ? " · " + esc(a.intent) : "") + (a.source ? " · " + (a.source === "ai" ? "AI" : "template") : "") + "</span><p>" + esc(a.text) + "</p></div>"
  ).join("") : '<p class="mute">Nothing sent or received yet.</p>';
}

async function refresh() {
  if (busy) return;
  try { render(await (await fetch("/api/state")).json()); }
  catch (e) { $("last").innerHTML = '<span class="bad">Atlas is not running.</span>'; }
}

document.addEventListener("click", async (e) => {
  const t = e.target;
  if (t.dataset && t.dataset.rename) {
    const name = window.prompt("Name for this phone", phoneName(t.dataset.rename));
    if (name) { await post("/api/rename", {phone: t.dataset.rename, name: name}); refresh(); }
  } else if (t.dataset && t.dataset.mark) {
    await post("/api/mark", {lead: Number(t.dataset.lead), outcome: t.dataset.mark}); refresh();
  }
});

$("run").onclick = async () => {
  busy = true; $("run").disabled = true; $("run").textContent = "Running…";
  try { const r = await post("/api/run"); if (r.busy) $("last").textContent = "Already running — try again in a moment."; }
  finally { busy = false; $("run").disabled = false; $("run").textContent = "Run now"; refresh(); }
};
$("auto").onchange = async (e) => { await post("/api/auto", {on: e.target.checked}); refresh(); };

function importResult(r) {
  if (r.error) return '<span class="bad">' + esc(r.error) + "</span>";
  const sk = Object.entries(r.skipped || {}).map((e) => e[1] + " " + e[0].replace(/_/g, " ")).join(", ");
  return '<span class="ok">Added ' + r.done + "</span>" + (sk ? ", skipped " + esc(sk) + " (duplicates stay on the phone that has them)" : "");
}
$("imp-go").onclick = async () => {
  let text = $("imp-text").value;
  const f = $("imp-file").files && $("imp-file").files[0];
  if (f) text = await f.text();
  if (!text.trim()) { $("imp-out").textContent = "Choose a file or paste contacts first."; return; }
  const r = await post("/api/import", {text: text, phone: $("imp-phone").value, list: $("imp-list").value});
  $("imp-out").innerHTML = importResult(r);
  if (r.done) { $("imp-text").value = ""; $("imp-file").value = ""; }
  refresh();
};
$("imp-wa").onclick = async () => {
  const r = await post("/api/import-whatsapp", {phone: $("imp-phone").value, list: $("imp-list").value});
  $("imp-out").innerHTML = importResult(r);
  refresh();
};
for (const id of ["w-mode", "w-claude", "w-ollama"]) {
  $(id).onchange = async () => {
    const w = await post("/api/writer", {writer: $("w-mode").value, claude_model: $("w-claude").value, ollama_model: $("w-ollama").value});
    $("w-status").innerHTML = writerHtml(w);
  };
}
refresh();
setInterval(refresh, 3000);
</script></body></html>
"""
