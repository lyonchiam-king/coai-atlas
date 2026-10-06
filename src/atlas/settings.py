"""Things the owner changes on the page: the Claude key, who he is, what he offers.

The key is write-only. It goes into `.env` and nothing ever reads it back out to
the page, a log or a report -- the page is told only whether one is set.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config
from .llm import read_env_file

KEY_NAME = "ANTHROPIC_API_KEY"
# What the owner may change about himself. Everything else in Config is a
# safety rule (caps, hours, pacing) and stays in code on purpose.
PROFILE_FIELDS = {"owner_name": 40, "sender_name": 60, "offer": 300, "opt_out_line": 120}


def set_env_key(env_file: str | Path, name: str, value: str | None) -> None:
    """Set or remove one KEY=value line, leaving every other line untouched.
    Written without a BOM: Python must read "ANTHROPIC_API_KEY", not "\\ufeffANTHROPIC_API_KEY"."""
    path = Path(env_file)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    kept = [l for l in lines if not re.match(rf"\s*{re.escape(name)}\s*=", l)]
    if value:
        kept.append(f"{name}={value}")
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def save_claude_key(env_file: str | Path, key: str) -> dict:
    key = (key or "").strip().strip('"').strip("'")
    if not key:
        return {"error": "Paste the key first."}
    if not key.startswith("sk-") or len(key) < 20 or any(c.isspace() for c in key):
        return {"error": "That does not look like a Claude key. They start with sk-ant-."}
    set_env_key(env_file, KEY_NAME, key)
    return {"ok": True}


def remove_claude_key(env_file: str | Path) -> dict:
    set_env_key(env_file, KEY_NAME, None)
    return {"ok": True}


def has_claude_key(env_file: str | Path) -> bool:
    import os
    return bool(os.environ.get(KEY_NAME) or read_env_file(env_file).get(KEY_NAME))


def test_claude(env_file: str | Path) -> dict:
    """Prove the key works without spending anything: listing models is free."""
    import os
    key = os.environ.get(KEY_NAME) or read_env_file(env_file).get(KEY_NAME)
    if not key:
        return {"ok": False, "message": "No Claude key saved yet."}
    try:
        import anthropic
    except ImportError:
        return {"ok": False, "message": "The Claude library is not installed. Run Setup.bat again."}
    try:
        anthropic.Anthropic(api_key=key, timeout=20.0, max_retries=1).models.list(limit=1)
    except anthropic.AuthenticationError:
        return {"ok": False, "message": "Anthropic rejected this key. Create a new one at console.anthropic.com."}
    except anthropic.PermissionDeniedError:
        return {"ok": False, "message": "The key works but is not allowed to use the API. Check billing at console.anthropic.com."}
    except anthropic.APIConnectionError:
        return {"ok": False, "message": "Could not reach Anthropic. Check the internet connection."}
    except anthropic.APIStatusError as e:
        return {"ok": False, "message": f"Anthropic answered {e.status_code}."}
    return {"ok": True, "message": "Key works."}


def load_profile(store) -> dict:
    try:
        return json.loads(store.get("profile") or "{}")
    except ValueError:
        return {}


def apply_profile(cfg: Config, store) -> None:
    for k, v in load_profile(store).items():
        if k in PROFILE_FIELDS and v:
            setattr(cfg, k, v)


def save_profile(cfg: Config, store, body: dict) -> dict:
    prof = load_profile(store)
    for k, limit in PROFILE_FIELDS.items():
        if k in body:
            v = str(body[k]).strip()[:limit]
            # Blank means "back to the default", never "no opt-out line": an empty
            # opt-out would quietly drop the one sentence PDPA asks for.
            if v:
                prof[k] = v
            else:
                prof.pop(k, None)
    store.put("profile", json.dumps(prof))
    defaults = Config()
    for k in PROFILE_FIELDS:
        setattr(cfg, k, prof.get(k) or getattr(defaults, k))
    return profile_state(cfg)


def profile_state(cfg: Config) -> dict:
    return {k: getattr(cfg, k) for k in PROFILE_FIELDS}
