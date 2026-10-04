"""WhatsApp via the local Baileys relay in whatsapp-relay/ (ported from Jarvis).

Phones are already E.164 here. The relay never guesses a country code.
"""
from __future__ import annotations

import json
import urllib.request


class RelayError(RuntimeError):
    pass


class RelayChannel:
    def __init__(self, url: str = "http://127.0.0.1:3001", timeout: float = 60):
        # Sends are paced inside the relay (15s apart by default) and wait for an
        # ack, so the timeout has to cover a queue, not a single message.
        self.url, self.timeout = url.rstrip("/"), timeout

    def _call(self, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.url}{path}", data, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:   # HTTPError on 4xx/5xx
            return json.loads(r.read() or b"{}")

    def send(self, phone: str, text: str) -> None:
        reply = self._call("/send", {"phone": phone, "message": text})
        # The relay answers 200 with status "failed" for errors inside sendMessage.
        # "unconfirmed" means the message left but WhatsApp has not acked yet: sent.
        if reply.get("status") not in ("sent", "unconfirmed"):
            raise RelayError(reply.get("error") or f"relay replied {reply!r}")

    def status(self) -> dict:
        return self._call("/status")

    def poll(self) -> list[dict]:
        return self._call("/inbox").get("messages", [])

    def ack(self, ids: list[str]) -> None:
        self._call("/inbox/ack", {"ids": ids})
