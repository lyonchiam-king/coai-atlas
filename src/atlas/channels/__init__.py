from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    def send(self, phone: str, text: str, typing_ms: int = 0, media: str = "") -> None:
        """Raise on failure. Returning means the channel accepted the message.

        typing_ms: how long to show "typing..." first, as a person would.
        media: a file name from the media library, sent with the text as its caption.
        """
