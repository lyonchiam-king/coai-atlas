"""When the next message may go out, paced like a person rather than a script.

A person doing outreach starts at a slightly different time each morning, stops
for lunch (later on a Friday, for Jumaat), sends one message, does something
else for a few minutes, sends another, occasionally gets pulled away for half an
hour, and stops before dinner. Sunday is off. They do not send the same number
every day. A burst of identical messages at machine-regular intervals is what
WhatsApp's spam detection looks for, and it is also what the recipient feels.

Every random choice comes from one injected `random.Random`, so a seeded test
gets the same day every time. The day's plan is stored in the database, so a
restart at 11:00 does not roll a new start time and a new quota.

This is pacing *inside* the guard's hard limits (09:00-21:00, daily cap), never
instead of them.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, time as dtime

from .config import Config
from .store import Store


def _hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


@dataclass
class DayPlan:
    date: str            # YYYY-MM-DD, local
    working: bool
    start: float = 0.0   # unix seconds
    end: float = 0.0
    lunch_start: float = 0.0
    lunch_end: float = 0.0
    quota: int = 0


class Pacer:
    def __init__(self, store: Store, cfg: Config, rng: random.Random | None = None):
        self.store, self.cfg = store, cfg
        self.rng = rng or random.Random()

    # ---- the day ---------------------------------------------------------

    def _at(self, day: datetime, t: dtime) -> float:
        return day.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0).timestamp()

    def _between(self, day: datetime, lo: str, hi: str) -> float:
        a, b = self._at(day, _hm(lo)), self._at(day, _hm(hi))
        return a + self.rng.random() * (b - a)

    def plan(self, now: float) -> DayPlan:
        local = datetime.fromtimestamp(now, self.cfg.tz)
        key = f"plan:{local.date().isoformat()}"
        saved = self.store.get(key)
        if saved:
            return DayPlan(**json.loads(saved))
        c = self.cfg
        if local.weekday() not in c.work_days:
            p = DayPlan(local.date().isoformat(), False)
        else:
            lunch = c.friday_lunch if local.weekday() == 4 else c.lunch
            lo = math.ceil(c.daily_cap * c.quota_floor)
            p = DayPlan(
                local.date().isoformat(), True,
                start=self._between(local, *c.start_between),
                end=self._between(local, *c.end_between),
                lunch_start=self._at(local, _hm(lunch[0])),
                lunch_end=self._at(local, _hm(lunch[1])),
                quota=self.rng.randint(min(lo, c.daily_cap), c.daily_cap),
            )
        self.store.put(key, json.dumps(p.__dict__))
        return p

    # ---- between messages ------------------------------------------------

    def gap_seconds(self) -> float:
        lo, typical, hi = self.cfg.gap_minutes
        if self.rng.random() < self.cfg.break_chance:
            return 60 * self.rng.uniform(*self.cfg.break_minutes)
        # Log-normal around the typical gap: mostly near it, now and then much
        # longer, never shorter than lo. Uniform jitter reads as a metronome.
        minutes = self.rng.lognormvariate(math.log(typical), 0.45)
        return 60 * min(hi, max(lo, minutes))

    def typing_ms(self, text: str) -> int:
        """How long the 'typing...' indicator shows before the message lands."""
        return int(min(18_000, self.rng.uniform(4_000, 9_000) + 25 * len(text)))

    def next_at(self) -> float | None:
        v = self.store.get("next_send_at")
        return float(v) if v else None

    def after_send(self, now: float) -> float:
        nxt = now + self.gap_seconds()
        self.store.put("next_send_at", repr(nxt))
        return nxt

    # ---- the decision ----------------------------------------------------

    def ready(self, now: float) -> tuple[bool, str]:
        if not self.cfg.human_pacing:
            return True, ""
        p = self.plan(now)
        if not p.working:
            return False, "day_off"
        if now < p.start:
            return False, "before_start"
        if p.lunch_start <= now < p.lunch_end:
            return False, "lunch"
        if now >= p.end:
            return False, "after_end"
        day_start = datetime.fromtimestamp(now, self.cfg.tz).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        if self.store.sends_since(day_start) >= p.quota:
            return False, "quota_reached"
        nxt = self.next_at()
        if nxt is not None and now < nxt:
            return False, "waiting"
        return True, ""
