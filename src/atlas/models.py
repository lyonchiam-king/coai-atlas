from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stage(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    FOLLOWUP_1 = "followup_1"
    FOLLOWUP_2 = "followup_2"
    REPLIED = "replied"      # a human takes over from here
    WON = "won"
    LOST = "lost"
    DO_NOT_CONTACT = "do_not_contact"


# Stages where the swarm may still message the lead. Everything else is a
# hand-off or a dead end, so no agent is ever allowed to write to it.
ACTIVE = {Stage.NEW, Stage.CONTACTED, Stage.FOLLOWUP_1}


@dataclass
class Lead:
    id: int
    name: str
    phone: str            # E.164, e.g. +60123456789
    stage: Stage = Stage.NEW
    company: str = ""
    notes: str = ""
    last_contacted: float | None = None   # unix seconds


@dataclass
class Draft:
    lead_id: int
    text: str
    kind: str             # first | followup_1 | followup_2


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
