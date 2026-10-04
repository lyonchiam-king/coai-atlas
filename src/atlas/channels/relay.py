"""WhatsApp via a local Baileys relay (same shape as Jarvis's). Phone is already E.164."""
from __future__ import annotations

import json
import urllib.request


class RelayChannel:
    def __init__(self, url: str = "http://127.0.0.1:3001", timeout: float = 30):
        self.url, self.timeout = url.rstrip("/"), timeout

    def send(self, phone: str, text: str) -> None:
        body = json.dumps({"phone": phone.lstrip("+"), "text": text}).encode()
        req = urllib.request.Request(f"{self.url}/send", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status >= 300:
                raise RuntimeError(f"relay answered {r.status}")
