"""Writes one message per lead, about that lead.

With an AI writer the message is built from what we know about the business
(the CSV's extra columns). Without one, or when the AI's answer fails the
checks below, a template is filled from the same facts -- in one of several
wordings, because identical text sent to many numbers is the pattern WhatsApp
flags as spam.
"""
from __future__ import annotations

import re
import zlib
from concurrent.futures import ThreadPoolExecutor

from ..llm import LLMError
from ..models import ACTIVE, Draft, Lead, Report, Stage
from . import Ctx

DAY = 86400

SYSTEM = """You write WhatsApp messages from {sender} to the owner of a small Malaysian business.
{sender} sells: {offer}.

Rules:
- Plain WhatsApp text. {lo}-{hi} words. No subject line, no bullet points, no links, no hashtags.
- Sound like one busy person writing to another: warm, direct, specific. No hype, no "I hope this finds you well".
- Mention at least one concrete fact about their business from the facts given, naturally.
- Use only the facts given. Never invent numbers, reviews, results, names or claims about them.
- At most one emoji, and only if it fits.
- Do not add an opt-out line or signature; one is appended automatically.
- Write the whole message in {language}.
- Reply with the message text only."""

TASKS = {
    "first": "Write the first message. Open with why you are writing to *them* specifically, "
             "then one sentence on how you could help, then a low-pressure question.",
    "followup_1": "They have not replied to the message below. Write a short follow-up that adds one "
                  "new useful angle for their kind of business. Do not repeat the first message "
                  "and do not guilt them about not replying.\n\nPrevious message:\n{previous}",
    "followup_2": "They have not replied to two messages. Write a brief, gracious last note that "
                  "leaves the door open, under 40 words.\n\nPrevious message:\n{previous}",
}

# Facts every lead has are passed separately; these keys are not repeated as facts.
CORE = {"name", "phone", "company"}


def check_message(text: str, cfg) -> str | None:
    """The AI's message, cleaned, or None if it must not be sent as-is."""
    t = text.strip().strip('"').strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    if not 40 <= len(t) <= 700:
        return None
    lowered = t.lower()
    bad = ("http", "www.", "[", "{", "subject:", "as an ai", cfg.opt_out_line.lower())
    if any(b in lowered for b in bad):
        return None
    # The opt-out wording is ours to add; a model writing its own "reply STOP" doubles it.
    # A word match, so "one-stop centre" in a real business description still passes.
    if re.search(r"(?<![\w-])stop(?![\w-])", lowered):
        return None
    return t


def _facts_block(lead: Lead) -> str:
    lines = [f"- Contact name: {lead.name or 'unknown'}"]
    if lead.company:
        lines.append(f"- Business: {lead.company}")
    for k, v in lead.facts.items():
        if k not in CORE and str(v).strip():
            lines.append(f"- {k.replace('_', ' ').capitalize()}: {str(v).strip()[:300]}")
    return "\n".join(lines)


def _template(lead: Lead, kind: str, ctx: Ctx) -> str:
    """English templates filled from the facts.

    `notes` are private context for the AI and never pasted into a message: "busy
    Instagram, owner hard to reach" is something you write about a lead, not to
    them. Only `hook` -- a sentence written *for* the message -- is used verbatim.
    """
    c, f = ctx.cfg, lead.facts
    who = lead.company or "your business"
    industry = f.get("industry", "").strip().lower()
    area = f.get("area", "").strip() or f.get("city", "").strip()
    hook = f.get("hook", "").strip()
    where = f" in {area}" if area else ""
    kind_of = f"{industry} businesses" if industry else "businesses like yours"
    owners = f"{industry} owners" if industry else "business owners"
    hi = f"Hi {lead.first_name}"
    lead_in = f" {hook.rstrip('.')}." if hook else ""
    options = {
        "first": [
            f"{hi}, {c.sender_name} here.{lead_in} I came across {who}{where} and wanted to say hello. "
            f"We build {c.offer}, and we've been working with {kind_of}. Would a quick chat this week be useful?",
            f"Hello {lead.first_name}! I'm {c.sender_name}.{lead_in} I noticed {who}{where} and thought of you: "
            f"we build {c.offer}, mostly for {kind_of}. Open to hearing how it could work for {who}?",
            f"{hi}, this is {c.sender_name}.{lead_in} We build {c.offer}, and I've been speaking with a few "
            f"{owners}{where} about it. Could I share a couple of ideas for {who}?",
        ],
        "followup_1": [
            f"{hi}, just floating this back up. For {kind_of}, the quickest win is usually answering "
            f"enquiries faster, which is where we help. Worth 10 minutes this week?",
            f"{hi}, following up on my note about {who}. Happy to show a short example of what we've set up "
            f"for other {owners}, no obligation.",
        ],
        "followup_2": [
            f"{hi}, I'll leave it here so I don't crowd your inbox. If it ever becomes a priority for {who}, "
            f"just message me. All the best!",
            f"Last note from me, {lead.first_name}. If the timing is ever right for {who}, I'm one message "
            f"away. Wishing you a good week!",
        ],
    }[kind]
    return re.sub(r"\s{2,}", " ", ctx.rng.choice(options)).strip()


def followup_jitter_days(lead_id: int) -> float:
    """0-1 extra days per lead, stable across restarts, so follow-ups don't land in waves."""
    return (zlib.crc32(str(lead_id).encode()) % 1000) / 1000


class Writer:
    name = "writer"

    def _due(self, lead: Lead, ctx: Ctx) -> str | None:
        gap = (ctx.now() - lead.last_contacted) / DAY if lead.last_contacted else None
        extra = followup_jitter_days(lead.id)
        if lead.stage is Stage.NEW:
            return "first"
        if lead.stage is Stage.CONTACTED and gap is not None and gap >= ctx.cfg.followup_1_after_days + extra:
            return "followup_1"
        if lead.stage is Stage.FOLLOWUP_1 and gap is not None and gap >= ctx.cfg.followup_2_after_days + extra:
            return "followup_2"
        return None

    def _draft(self, lead: Lead, kind: str, ctx: Ctx) -> tuple[Draft, str]:
        c = ctx.cfg
        body, source, note = None, "template", ""
        if ctx.llm is not None:
            lang = c.languages.get(str(lead.facts.get("language", "en")).strip().lower(), "English")
            lo, hi = (20, 40) if kind == "followup_2" else (35, 70)
            system = SYSTEM.format(sender=c.sender_name, offer=c.offer, lo=lo, hi=hi, language=lang)
            prompt = (f"Facts about this business:\n{_facts_block(lead)}\n\n"
                      + TASKS[kind].format(previous=ctx.store.last_sent_text(lead.id) or "(not available)"))
            for _ in range(2):   # one retry for an answer that fails the checks
                try:
                    body = check_message(ctx.llm.complete(system, prompt), c)
                except LLMError as e:
                    note = f"ai_failed: {e}"
                    break
                if body:
                    source = "ai"
                    break
                note = "ai_rejected_by_checks"
        if not body:
            body = _template(lead, kind, ctx)
        return Draft(lead.id, f"{body}\n\n{c.opt_out_line}", kind, source), note

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        pending = ctx.store.pending_drafts()
        queued = {d.lead_id for _, d in pending}
        room = max(0, ctx.cfg.max_queued_drafts - len(pending))
        todo: list[tuple[Lead, str]] = []
        for lead in ctx.store.leads():
            if lead.stage not in ACTIVE or lead.id in queued:
                rep.skip("not_active_or_queued")
            elif (kind := self._due(lead, ctx)) is None:
                rep.skip("not_due")
            elif len(todo) >= room:
                rep.skip("queue_full")   # written later, closer to when it can actually go
            else:
                todo.append((lead, kind))
        # Drafting is the slow, model-bound part, so it fans out; the store call is locked.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for d, note in pool.map(lambda t: self._draft(*t, ctx), todo):
                ctx.store.add_draft(d)
                rep.done += 1
                if note:
                    rep.skip(note)
        return rep
