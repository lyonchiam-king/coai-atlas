"""The owner's own message templates, one set per message type.

A template can hold several versions separated by a line containing only ---,
placeholders such as {first_name}, and spintax such as {Hi|Hello|Hey}. Every
message picks a version, spins it and fills it, so no two people get the same
text -- identical text to many numbers is what WhatsApp flags.

With "AI personalises" on, the writer then makes small changes for the person.
Small is enforced, not hoped for: the AI's version must keep every link and
number, stay close in length and keep most of the owner's words, or the filled
template goes out as written.
"""
from __future__ import annotations

import json
import re

from .models import Lead

KINDS = ("opener", "opener_nudge", "pitch", "followup_1", "followup_2", "first")
PLACEHOLDERS = ("first_name", "name", "company", "owner", "how_we_know", "industry", "area")
SPIN = re.compile(r"\{([^{}]*\|[^{}]*)\}")
FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_ ]*)\}")
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
# Dates, prices, phone numbers: start and end on a digit, so "10," and "10 -" are the same 10.
NUMBER = re.compile(r"\d[\d,./:\-]*\d")

AMEND_SYSTEM = """You lightly personalise a WhatsApp message that {owner} wrote, for one person.
Rules:
- Keep the meaning, the offer, the tone and the language of the original. Keep the length within 20%.
- Change only a few words: the greeting, a phrase that fits what is known about them, the flow.
- Keep every link, price, phone number and name exactly as written.
- Use only the facts given about them. Never invent shared memories, results or claims.
- Do not add an opt-out line or a signature.
- Reply with the message text only."""


def load(store) -> dict:
    try:
        data = json.loads(store.get("templates") or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if k in KINDS and isinstance(v, dict)}


def save(store, kind: str, text: str, ai: bool, media: bool) -> dict:
    if kind not in KINDS:
        return {"error": "unknown message type"}
    data = load(store)
    text = text.replace("\r\n", "\n").strip()[:4000]
    if text:
        data[kind] = {"text": text, "ai": bool(ai), "media": bool(media)}
    else:
        data.pop(kind, None)       # empty = go back to Atlas's own wording
    store.put("templates", json.dumps(data, ensure_ascii=False))
    return {"ok": True, "templates": data}


def variants(text: str) -> list[str]:
    parts = re.split(r"(?m)^\s*-{3,}\s*$", text)
    return [p.strip() for p in parts if p.strip()]


def spin(text: str, rng) -> str:
    # Innermost first, so {Hi|{Hello|Hey}} works.
    while True:
        new = SPIN.sub(lambda m: rng.choice(m.group(1).split("|")), text, count=1)
        if new == text:
            return text
        text = new


def fill(text: str, lead: Lead, cfg) -> str:
    f = {k.lower(): str(v) for k, v in lead.facts.items()}
    values = {
        "first_name": lead.first_name if lead.name.strip() else "",
        "name": lead.name,
        "company": lead.company,
        "owner": cfg.owner_name,
        "how_we_know": f.get("how_we_know", "") or f.get("relationship", ""),
        "industry": f.get("industry", ""),
        "area": f.get("area", "") or f.get("city", ""),
    }

    def sub(m):
        key = m.group(1).strip().lower().replace(" ", "_")
        return values.get(key, f.get(key, "")).strip()

    out = FIELD.sub(sub, text)
    # A missing value must not leave "Hi ,", "( )" or double spaces behind.
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"[ \t]+([,.!?;:])", r"\1", out)
    out = re.sub(r"([,])(?=[,.!?])", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return "\n".join(l.strip() for l in out.splitlines()).strip()


def build(template: dict, lead: Lead, cfg, rng) -> str:
    return fill(spin(rng.choice(variants(template["text"])), rng), lead, cfg)


def close_enough(base: str, amended: str) -> bool:
    """Is the AI's version still the owner's message, lightly changed?"""
    a = amended.strip().strip('"').strip()
    if not a or not 0.6 <= len(a) / max(1, len(base)) <= 1.5:
        return False
    for must in URL.findall(base) + NUMBER.findall(base):
        if must not in a:
            return False
    if re.search(r"(?<![\w-])stop(?![\w-])", a.lower()) and not re.search(r"(?<![\w-])stop(?![\w-])", base.lower()):
        return False
    words = set(re.findall(r"\w+", base.lower()))
    kept = words & set(re.findall(r"\w+", a.lower()))
    return len(kept) >= 0.4 * len(words)
