from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    def send(self, phone: str, text: str) -> None:
        """Raise on failure. Returning means the channel accepted the message."""
