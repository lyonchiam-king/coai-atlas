from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone, timedelta


@dataclass
class Config:
    country: str = "MY"
    tz: timezone = timezone(timedelta(hours=8))   # Penang has no DST
    send_from_hour: int = 9                        # local time, inclusive
    send_until_hour: int = 21                      # exclusive
    daily_cap: int = 20      # a new WhatsApp number is banned fast; start low
    followup_1_after_days: float = 2.0
    followup_2_after_days: float = 5.0
    sender_name: str = "Lyon from COAI"
    offer: str = "AI tools that help Malaysian SMEs get more customers"
    # Appended by code, never asked of the model: a model rewords or drops it.
    opt_out_line: str = "Reply STOP and I won't message again."
