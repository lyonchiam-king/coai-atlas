"""The AI writer: Claude (paid, best writing) or Ollama (free, local), or neither.

Claude uses the official `anthropic` SDK -- the one dependency outside the
standard library, imported lazily so a machine without it still runs. Ollama is
plain HTTP to the local Ollama app. Which one writes is a setting on the page;
with neither available, Atlas writes from templates and says so.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

# Offered on the page. The default is the best writer; the others trade quality for cost.
CLAUDE_MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 (best writing)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (cheaper)",
    "claude-haiku-4-5": "Claude Haiku 4.5 (cheapest)",
}
# Haiku 4.5 rejects `effort`, and server-side fallbacks are offered on the 5.x models only.
_NO_EFFORT = {"claude-haiku-4-5"}
_FALLBACKS = {"claude-opus-5-5", "claude-sonnet-5-5"}


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

    def request(self, system: str, prompt: str) -> dict:
        kw: dict = {
            "model": self.model,
            "max_tokens": 4000,                   # room for thinking; the message itself is short
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.model not in _NO_EFFORT:
            kw["output_config"] = {"effort": "low"}   # a 60-word message is not hard reasoning
        if self.model in _FALLBACKS:
            # A safety classifier may decline; let the API retry on its recommended model.
            kw["betas"] = ["server-side-fallback-2026-07-01"]
            kw["fallbacks"] = "default"
        return kw

    def complete(self, system: str, prompt: str) -> str:
        import anthropic
        try:
            msg = self.client.beta.messages.create(**self.request(system, prompt))
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


class OllamaLLM:
    """A model running in the Ollama app on this PC. Free; quality depends on the model."""

    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", timeout: float = 180):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout
        self.name = f"Ollama ({model})"

    @staticmethod
    def list_models(url: str = "http://127.0.0.1:11434", timeout: float = 3) -> list[str]:
        """What this PC actually has. Never a name typed from memory: one that is not
        pulled simply fails, and the page offers only what is here."""
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout) as r:
                return sorted(m["name"] for m in json.loads(r.read()).get("models", []))
        except (OSError, ValueError, KeyError):
            return []

    def complete(self, system: str, prompt: str) -> str:
        body = json.dumps({
            "model": self.model, "stream": False, "options": {"temperature": 0.8},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(f"{self.url}/api/chat", body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                reply = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"Ollama answered {e.code} (is {self.model!r} pulled?)") from None
        except (OSError, ValueError):
            raise LLMError("Ollama is not running on this PC") from None
        text = (reply.get("message") or {}).get("content", "")
        # Reasoning models (qwen3, deepseek-r1) put their thinking in <think> tags.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if not text:
            raise LLMError("Ollama returned nothing")
        return text


def make_writer(settings: dict, env_file: str | Path = ".env"):
    """The writer the page's settings ask for, or None for templates. Never raises:
    a missing key or a stopped Ollama means templates, and the page says which."""
    mode = settings.get("writer", "auto")
    claude_model = settings.get("claude_model") or "claude-opus-5-5"
    if mode in ("auto", "claude"):
        w = ClaudeLLM.from_env(claude_model, env_file)
        if w or mode == "claude":
            return w
    if mode in ("auto", "ollama"):
        url = settings.get("ollama_url") or "http://127.0.0.1:11434"
        model = settings.get("ollama_model") or ""
        if model and (mode == "ollama" or model in OllamaLLM.list_models(url)):
            return OllamaLLM(model, url)
    return None
