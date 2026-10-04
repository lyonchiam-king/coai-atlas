"""The one place that decides whether a message may leave. Agents never bypass it."""
from __future__ import annotations

from datetime import datetime

from .config import Config
from .models import ACTIVE, Lead
from .store import Store

STOP_WORDS = {"stop", "unsubscribe", "berhenti", "jangan", "remove me", "opt out", "optout", "tak nak"}


def is_opt_out(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in STOP_WORDS)


def may_send(lead: Lead, store: Store, cfg: Config, now: float) -> tuple[bool, str]:
    if lead.stage not in ACTIVE:
        return False, f"stage_{lead.stage.value}"
    local = datetime.fromtimestamp(now, cfg.tz)
    if not cfg.send_from_hour <= local.hour < cfg.send_until_hour:
        return False, "quiet_hours"
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if store.sends_since(day_start) >= cfg.daily_cap:
        return False, "daily_cap"
    return True, ""
