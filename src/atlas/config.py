from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta, timezone


@dataclass
class Config:
    country: str = "MY"
    tz: timezone = timezone(timedelta(hours=8))   # Penang has no DST
    # Hard outer bounds, enforced by the guard whatever pacing says.
    send_from_hour: int = 9                        # local time, inclusive
    send_until_hour: int = 21                      # exclusive
    daily_cap: int = 20      # a new WhatsApp number is banned fast; start low
    followup_1_after_days: float = 2.0
    followup_2_after_days: float = 5.0
    sender_name: str = "Lyon from COAI"
    offer: str = "AI tools that help Malaysian SMEs get more customers"
    # Appended by code, never asked of the model: a model rewords or drops it.
    opt_out_line: str = "Reply STOP and I won't message again."

    # --- Human pacing (pacing.py). Off only in tests that check other rules. ---
    human_pacing: bool = True
    work_days: tuple[int, ...] = (0, 1, 2, 3, 4, 5)          # Mon-Sat; Sunday off
    start_between: tuple[str, str] = ("09:15", "10:30")       # first message of the day
    end_between: tuple[str, str] = ("17:30", "18:45")         # last message of the day
    lunch: tuple[str, str] = ("12:30", "14:00")
    friday_lunch: tuple[str, str] = ("12:15", "14:45")        # Jumaat prayers
    gap_minutes: tuple[float, float, float] = (3, 7, 25)      # min, typical, max between sends
    break_chance: float = 0.12                                # sometimes a longer pause
    break_minutes: tuple[float, float] = (25, 50)
    quota_floor: float = 0.6     # each day sends between 60% and 100% of the cap
    max_queued_drafts: int = 40  # don't pay to write messages that won't go out this week

    # --- AI writer (llm.py). Blank key = varied templates, no AI. ---
    model: str = "claude-opus-5-5"
    languages: dict[str, str] = field(default_factory=lambda: {
        "en": "English", "ms": "Bahasa Malaysia", "bm": "Bahasa Malaysia",
        "zh": "Simplified Chinese", "cn": "Simplified Chinese"})
