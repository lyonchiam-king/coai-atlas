from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stage(str, Enum):
    NEW = "new"
    # Two-step reconnect: "is this still you?" first, the pitch only after they answer.
    OPENER_SENT = "opener_sent"
    OPENER_NUDGED = "opener_nudged"   # one light nudge, then the swarm waits for ever
    CONFIRMED = "confirmed"           # they answered as themselves; the pitch is next
    CONTACTED = "contacted"           # pitch (or one-step first message) sent
    FOLLOWUP_1 = "followup_1"
    FOLLOWUP_2 = "followup_2"
    REPLIED = "replied"      # a human takes over from here
    WON = "won"
    LOST = "lost"
    DO_NOT_CONTACT = "do_not_contact"
    WRONG_NUMBER = "wrong_number"     # the number belongs to someone else now
    NOT_ON_WHATSAPP = "not_on_whatsapp"


# Stages where the swarm may still message the lead. Everything else is a
# hand-off or a dead end, so no agent is ever allowed to write to it.
ACTIVE = {Stage.NEW, Stage.OPENER_SENT, Stage.CONFIRMED, Stage.CONTACTED, Stage.FOLLOWUP_1}
# A reply in these stages answers "is this still you?", so it is read differently.
AWAITING_CONFIRM = {Stage.OPENER_SENT, Stage.OPENER_NUDGED}


@dataclass
class Lead:
    id: int
    name: str
    phone: str            # E.164, e.g. +60123456789
    stage: Stage = Stage.NEW
    company: str = ""
    notes: str = ""
    last_contacted: float | None = None   # unix seconds
    # Everything else we know, straight from the CSV: industry, area, rating,
    # language, anything. The writer turns these into a message about *them*.
    facts: dict[str, str] = field(default_factory=dict)
    account: str = "1"        # which phone this person belongs to; only that phone writes
    list_name: str = ""
    wa: str = ""              # "yes" / "no" once the number has been checked on WhatsApp

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else "there"


@dataclass
class Draft:
    lead_id: int
    text: str
    kind: str             # opener | opener_nudge | pitch | first | followup_1 | followup_2
    source: str = "template"   # "ai", "template" (Atlas's) or "yours" / "yours+ai", shown on the page
    media: str = ""            # file name in the media library, or "" for text only


@dataclass
class Inbound:
    lead_id: int
    text: str
    at: float
    intent: str = ""      # filled by the Responder


@dataclass
class Report:
    agent: str
    done: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1
