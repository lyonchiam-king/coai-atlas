"""The AI writer's connection to Claude. Optional: with no key, Atlas uses templates.

Uses the official `anthropic` SDK. It is the one dependency outside the standard
library, so it is imported lazily -- a machine without it still runs, and says
on the page that messages come from templates.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str) -> str:
        """Return the message text, or raise LLMError. Never return a refusal as text."""


def read_env_file(path: str | Path = ".env") -> dict[str, str]:
    """KEY=value lines. Values are returned, never printed or logged anywhere."""
    out: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


class ClaudeLLM:
    def __init__(self, model: str, client=None):
        self.model = model
        self.name = f"Claude ({model})"
        if client is None:
            import anthropic   # optional dependency
            client = anthropic.Anthropic(timeout=60.0, max_retries=2)
        self.client = client

    @classmethod
    def from_env(cls, model: str, env_file: str | Path = ".env") -> "ClaudeLLM | None":
        """A writer when a key and the SDK are both present, else None (templates)."""
        key = os.environ.get("ANTHROPIC_API_KEY") or read_env_file(env_file).get("ANTHROPIC_API_KEY")
        if not key:
            return None
        try:
            import anthropic
        except ImportError:
            return None
        return cls(model, anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=2))

    def complete(self, system: str, prompt: str) -> str:
        import anthropic
        try:
            msg = self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,                  # room for thinking; the message itself is short
                output_config={"effort": "low"},  # a 60-word message is not hard reasoning
                # A safety classifier may decline; let the API retry on its recommended model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.NotFoundError as e:
            raise LLMError(f"model {self.model!r} not available to this key") from e
        except anthropic.AuthenticationError as e:
            raise LLMError("the API key was rejected") from e
        except anthropic.RateLimitError as e:
            raise LLMError("rate limited by the API") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("could not reach the API") from e
        if msg.stop_reason == "refusal":
            raise LLMError("the model declined")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise LLMError(f"empty reply (stop_reason {msg.stop_reason})")
        return text
