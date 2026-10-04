import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from atlas.channels.relay import RelayChannel, RelayError
from atlas.config import Config
from atlas.models import Inbound, Stage
from atlas.store import Store
from atlas.swarm import Swarm
from test_swarm import Clock, FakeChannel, T0, DAY

RELAY = Path(__file__).parent.parent / "whatsapp-relay"


class FakeRelay:
    """A real HTTP server speaking the relay's protocol, so RelayChannel is exercised for real."""

    def __init__(self):
        self.sends, self.acked, self.inbox, self.checked = [], [], [], []
        self.send_reply, self.send_code = {"status": "sent"}, 200
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _json(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/inbox":
                    return self._json(200, {"messages": outer.inbox})
                if self.path == "/contacts":
                    return self._json(200, {"contacts": [{"phone": "+60123456789", "name": "Aisha", "notify": "", "last_chat": 1}]})
                self._json(200, {"connected": True})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/send":
                    outer.sends.append(body)
                    self._json(outer.send_code, outer.send_reply)
                elif self.path == "/check":
                    outer.checked.append(body["phones"])
                    self._json(200, {"results": {p: {"registered": p.endswith("9")} for p in body["phones"]}})
                elif self.path == "/profile":
                    self._json(200, {"about": "Hey there", "business": {"category": "Bakery"}})
                else:
                    outer.acked += body["ids"]
                    self._json(200, {"removed": len(body["ids"])})

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def relay():
    r = FakeRelay()
    yield r
    r.close()


def test_send_uses_the_field_names_the_relay_reads(relay):
    RelayChannel(relay.url).send("+60123456789", "hello", typing_ms=5000)
    assert relay.sends == [{"phone": "+60123456789", "message": "hello", "typing_ms": 5000}]


def test_unconfirmed_counts_as_sent_but_failed_and_http_errors_raise(relay):
    ch = RelayChannel(relay.url)
    relay.send_reply = {"status": "unconfirmed"}
    ch.send("+60123456789", "x")                      # message left; no raise
    relay.send_reply = {"status": "failed", "error": "boom"}
    with pytest.raises(RelayError, match="boom"):     # 200 with status failed
        ch.send("+60123456789", "x")
    relay.send_code, relay.send_reply = 503, {"status": "failed", "error": "WhatsApp is not connected"}
    with pytest.raises(RelayError, match="not connected") as e:   # the relay's words, not "HTTPError"
        ch.send("+60123456789", "x")
    assert e.value.stop_batch


def test_relay_down_stops_the_batch_with_a_readable_reason(relay):
    store = Store()
    for i in range(3):
        store.set_wa(store.add_lead(f"L{i}", f"+6012345678{i}"), "yes")   # already checked
    relay.send_code, relay.send_reply = 503, {"status": "failed", "error": "WhatsApp is not connected"}
    reports = Swarm(store, RelayChannel(relay.url), Config(human_pacing=False, two_step=False), now=Clock()).tick()
    assert reports[-1].skipped == {"send_failed: WhatsApp is not connected": 1}
    assert len(relay.sends) == 1                      # did not hammer a dead relay three times
    assert all(l.stage is Stage.NEW for l in store.leads())


def test_relay_not_running_is_named():
    with pytest.raises(RelayError, match="not running"):
        RelayChannel("http://127.0.0.1:9", timeout=2).send("+60123456789", "x")


def test_a_stop_from_whatsapp_reaches_the_lead_and_is_acked(relay):
    store, clock = Store(), Clock()
    lid = store.add_lead("Aisha", "+60123456789")
    ch = RelayChannel(relay.url)
    swarm = Swarm(store, ch, Config(human_pacing=False, two_step=False), now=clock, inbox=ch)
    swarm.tick()                                       # first message goes out
    relay.inbox = [{"id": "W1", "phone": "+60123456789", "text": "stop", "at": clock.t},
                   {"id": "W2", "phone": "+6599999999", "text": "wrong number?", "at": clock.t}]
    clock.t += 3 * DAY
    swarm.tick()
    assert store.lead(lid).stage is Stage.DO_NOT_CONTACT
    assert len(relay.sends) == 1                       # no follow-up after STOP
    assert relay.acked == ["W1", "W2"]                 # stranger acked too, not re-read forever


def test_redelivered_reply_is_stored_once():
    store = Store()
    lid = store.add_lead("Aisha", "+60123456789")
    assert store.add_inbound(Inbound(lid, "hi", 1.0), "W1") is True
    assert store.add_inbound(Inbound(lid, "hi", 1.0), "W1") is False


def test_reply_is_not_acked_if_storing_it_crashes():
    class Src:
        acked = None
        def poll(self): return [{"id": "W1", "phone": "+60123456789", "text": "stop", "at": 1}]
        def ack(self, ids): Src.acked = ids

    store = Store()
    store.add_lead("Aisha", "+60123456789")
    store.add_inbound = lambda *a, **k: 1 / 0
    swarm = Swarm(store, FakeChannel(), inbox=Src())
    swarm.tick()
    assert Src.acked is None                           # re-delivered next time, never lost


def test_the_ported_relay_keeps_the_fixes_jarvis_learned_the_hard_way():
    raw = (RELAY / "server.js").read_text()
    # Comments explain the old "60" bug in words; only code may not contain it.
    js = "\n".join(l for l in raw.splitlines() if not l.strip().startswith(("//", "*", "/*")))
    assert "getMessage" in js                          # retry receipts, or "Waiting for this message"
    assert "sock.onWhatsApp(digits)" in js             # digits, not a JID
    assert '"60"' not in js and "'60'" not in js       # never force a country code
    assert js.index("app.listen") < js.index("connectSafely();", js.index("app.listen"))
    assert "EADDRINUSE" in js                          # port is the single-instance lock
    assert "generation" in js                          # superseded sockets are inert
    assert "messages.upsert" in js and "/inbox/ack" in js
    # typing is capped and cosmetic: it sits inside its own try, before the send
    assert "Math.min(TYPING_MAX_MS" in js
    assert js.index('sendPresenceUpdate("composing"') < js.index("sock.sendMessage(jid, payload)")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inbound_parsing_under_node():
    r = subprocess.run(["node", "--test"], cwd=RELAY, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_profile_and_contacts_speak_the_relays_protocol(relay):
    ch = RelayChannel(relay.url)
    assert ch.check(["+60123456789", "+60123456780"]) == {
        "+60123456789": {"registered": True}, "+60123456780": {"registered": False}}
    assert relay.checked == [["+60123456789", "+60123456780"]]
    assert ch.profile("+60123456789")["business"]["category"] == "Bakery"
    assert ch.contacts()[0]["name"] == "Aisha"


def test_relay_profile_and_contacts_routes_are_wired():
    raw = (RELAY / "server.js").read_text()
    for needle in ('app.get("/contacts"', 'app.post("/profile"', "sock.fetchStatus(jid)",
                   "sock.getBusinessProfile(jid)", '"messaging-history.set"', "contacts.addChats(chats)"):
        assert needle in raw, needle
