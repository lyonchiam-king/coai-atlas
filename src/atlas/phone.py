"""Phone numbers are normalised once, with the country known, never guessed."""
from __future__ import annotations

import re

DIAL_CODES = {"MY": "60", "SG": "65", "ID": "62", "TH": "66", "GB": "44"}


def to_e164(raw: str, country: str = "MY") -> str | None:
    """Return +<digits>, or None if the number cannot be dialled.

    A leading + or 00 means the number already names its country and is left
    alone. Only a national number gets the campaign country's code.
    """
    s = raw.strip()
    plus = s.startswith("+") or s.startswith("00")
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None
    if not plus:
        code = DIAL_CODES.get(country)
        if code is None:
            return None
        digits = code + digits.lstrip("0")
    return "+" + digits if 8 <= len(digits) <= 15 else None
