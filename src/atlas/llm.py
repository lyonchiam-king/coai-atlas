from __future__ import annotations

from typing import Protocol


class LLM(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


class TemplateLLM:
    """Offline fallback so the swarm runs, and tests pass, with no model or key."""

    def complete(self, system: str, prompt: str) -> str:
        return prompt
