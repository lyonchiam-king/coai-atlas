"""Writes one message per person, about that person.

These are the owner's own contacts -- people who once knew him -- so the
default flow is a reconnect, not a cold pitch:

    opener        "Hi Aisha, is this still your number? Lyon here."   (no selling)
    opener_nudge  one light nudge a few days later, then silence
    pitch         only after they answer as themselves; replies to what they said
    followup_1/2  if the pitch goes unanswered

A long-lost number may belong to a stranger now. Pitching a stranger is spam
and gets the phone reported; asking "is this still you?" is what a person does.

With an AI writer every message is built from what we know (the list's extra
columns plus the free WhatsApp profile). Without one, or when the AI's answer
fails the checks, a template is filled from the same facts -- in one of several
wordings, because identical text sent to many numbers is what WhatsApp flags.
"""
from __future__ import annotations

import re
import zlib
from concurrent.futures import ThreadPoolExecutor

from ..llm import LLMError
from ..models import ACTIVE, Draft, Lead, Report, Stage
from . import Ctx

DAY = 86400

# Only messages that sell carry the opt-out line. "Is this still your number?"
# with "Reply STOP" under it reads like a robot, and it offers nothing to opt out of.
SELLING = {"pitch", "first", "followup_1", "followup_2"}

RECONNECT_SYSTEM = """You write a short WhatsApp message from {owner} to someone {owner} used to know but has not spoken to in a long time.
The only goal is to check the number still belongs to them and restart the conversation.

Rules:
- {lo}-{hi} words. Casual, warm, Malaysian. Plain text, no links, no hashtags, at most one emoji.
- Do NOT sell or mention any business, product or service.
- Use only the facts given about how you know them. Never invent shared memories.
- Write the whole message in {language}.
- Reply with the message text only."""

SALES_SYSTEM = """You write WhatsApp messages from {sender} to someone {owner} knows personally.
{owner} has started COAI, which offers: {offer}.

Rules:
- Plain WhatsApp text. {lo}-{hi} words. No subject line, no bullet points, no links, no hashtags.
- Sound like one person writing to someone they know: warm, direct, specific. No hype, no "I hope this finds you well".
- Use what is known about them naturally. Use only the facts given. Never invent numbers, results, names or claims.
- At most one emoji, and only if it fits.
- Do not add an opt-out line or signature; one is appended automatically.
- Write the whole message in {language}.
- Reply with the message text only."""

TASKS = {
    "opener": "Write the first message in years: greet them by first name, say who you are "
              "(and how you know each other, if the facts say), and ask whether this is still their number.",
    "opener_nudge": "They have not answered this message:\n{previous}\n\nWrite one light nudge, "
                    "under 20 words. No guilt, no selling.",
    "pitch": "They just answered your reconnect message, replying:\n\"{reply}\"\n\n"
             "Write your reply: respond to what they actually said first, then briefly share that you "
             "started COAI and what it does, then ask whether it could help their business or whether they "
             "know someone it would suit. One question only.",
    "first": "Write the first message. Open with why you are writing to *them* specifically, "
             "then one sentence on how you could help, then a low-pressure question.",
    "followup_1": "They have not replied to the message below. Write a short follow-up that adds one "
                  "new useful angle for their kind of business. Do not repeat it "
                  "and do not guilt them about not replying.\n\nPrevious message:\n{previous}",
    "followup_2": "They have not replied to two messages. Write a brief, gracious last note that "
                  "leaves the door open, under 40 words.\n\nPrevious message:\n{previous}",
}

WORDS = {"opener": (8, 35), "opener_nudge": (4, 20), "pitch": (40, 90), "first": (35, 70),
         "followup_1": (30, 60), "followup_2": (15, 40)}
CHARS = {"opener": (15, 260), "opener_nudge": (8, 160)}   # everything else: 40-700

# Facts every lead has are passed separately; these keys are not repeated as facts.
CORE = {"name", "phone", "company"}


def check_message(text: str, cfg, kind: str = "first") -> str | None:
    """The AI's message, cleaned, or None if it must not be sent as-is."""
    t = text.strip().strip('"').strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    lo, hi = CHARS.get(kind, (40, 700))
    if not lo <= len(t) <= hi:
        return None
    lowered = t.lower()
    bad = ("http", "www.", "[", "{", "subject:", "as an ai", cfg.opt_out_line.lower())
    if any(b in lowered for b in bad):
        return None
    # The opt-out wording is ours to add; a model writing its own "reply STOP" doubles it.
    # Hyphen-aware, so "one-stop centre" in a real business description still passes.
    if re.search(r"(?<![\w-])stop(?![\w-])", lowered):
        return None
    if kind in ("opener", "opener_nudge") and "coai" in lowered:
        return None   # the reconnect must not sell; the model was told and did anyway
    return t


def _facts_block(lead: Lead) -> str:
    lines = [f"- Name: {lead.name or 'unknown'}"]
    if lead.company:
        lines.append(f"- Business: {lead.company}")
    for k, v in lead.facts.items():
        if k not in CORE and str(v).strip():
            lines.append(f"- {k.replace('_', ' ').capitalize()}: {str(v).strip()[:300]}")
    return "\n".join(lines)


def _lang_code(lead: Lead) -> str:
    code = str(lead.facts.get("language", "en")).strip().lower()
    return {"bm": "ms", "cn": "zh"}.get(code, code)


def _template(lead: Lead, kind: str, ctx: Ctx) -> str:
    """Templates filled from the facts.

    `notes` are private context for the AI and never pasted into a message: "busy
    Instagram, owner hard to reach" is something you write about a lead, not to
    them. Only `hook` -- a sentence written *for* the message -- is used verbatim.
    Openers come in English, Malay and Chinese because they are the first thing an
    old friend reads; the rest is English when no AI writer is available.
    """
    c, f = ctx.cfg, lead.facts
    first, owner = lead.first_name, c.owner_name
    how = (f.get("how_we_know", "") or f.get("relationship", "")).strip()
    lang = _lang_code(lead)
    if kind in ("opener", "opener_nudge"):
        ctx_en = f", from {how}" if how else ""
        ctx_ms = f", dari {how}" if how else ""
        ctx_zh = f"（{how}）" if how else ""
        options = {
            ("opener", "en"): [
                f"Hi {first}, is this still your number? It's {owner} here{ctx_en}. Been a long time!",
                f"Hello {first}! {owner} here{ctx_en}. Just checking if this is still you? Long time no see 🙂",
                f"Hi {first}, {owner} here{ctx_en}. Is this number still yours? Hope you've been well!",
            ],
            ("opener", "ms"): [
                f"Hai {first}, ini masih nombor {first} ke? {owner} sini{ctx_ms}. Lama tak dengar khabar!",
                f"Salam {first}, {owner} sini{ctx_ms}. Nombor ni masih {first} punya ke? 🙂",
            ],
            ("opener", "zh"): [
                f"Hi {first}，请问这还是你的号码吗？我是{owner}{ctx_zh}。好久不见！",
                f"{first}你好！我是{owner}{ctx_zh}，想确认一下这还是你的号码吗？🙂",
            ],
            ("opener_nudge", "en"): [f"Hi {first}, just checking this reached you 🙂",
                                     f"{first}, hope this is still you! No rush 🙂"],
            ("opener_nudge", "ms"): [f"Hai {first}, cuma nak pastikan mesej saya sampai 🙂"],
            ("opener_nudge", "zh"): [f"Hi {first}，想确认你有收到我的信息吗？🙂"],
        }
        pick = options.get((kind, lang)) or options[(kind, "en")]
        return ctx.rng.choice(pick)

    who = lead.company or "your business"
    industry = f.get("industry", "").strip().lower()
    area = f.get("area", "").strip() or f.get("city", "").strip()
    hook = f.get("hook", "").strip()
    where = f" in {area}" if area else ""
    kind_of = f"{industry} businesses" if industry else "businesses like yours"
    owners = f"{industry} owners" if industry else "business owners"
    hi = f"Hi {first}"
    lead_in = f" {hook.rstrip('.')}." if hook else ""
    options = {
        "pitch": [
            f"So good to hear from you, {first}!{lead_in} Quick update from my side: I've started COAI. "
            f"We build {c.offer}. Could something like that help {who}, or do you know someone it would suit?",
            f"Great to reconnect, {first}!{lead_in} I'm running COAI now: we build {c.offer}. "
            f"Would it be useful for {who}? Happy to share more if you're curious.",
        ],
        "first": [
            f"{hi}, {c.sender_name} here.{lead_in} I came across {who}{where} and wanted to say hello. "
            f"We build {c.offer}, and we've been working with {kind_of}. Would a quick chat this week be useful?",
            f"Hello {first}! I'm {c.sender_name}.{lead_in} I noticed {who}{where} and thought of you: "
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
            f"Last note from me, {first}. If the timing is ever right for {who}, I'm one message "
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
        c = ctx.cfg
        gap = (ctx.now() - lead.last_contacted) / DAY if lead.last_contacted else None
        extra = followup_jitter_days(lead.id)
        if lead.stage is Stage.NEW:
            # On a phone that can check, nothing is written until WhatsApp says "yes".
            if lead.account in ctx.checked_accounts and lead.wa != "yes":
                return None
            return "opener" if c.two_step else "first"
        if lead.stage is Stage.OPENER_SENT and gap is not None and gap >= c.nudge_after_days + extra:
            return "opener_nudge"
        if lead.stage is Stage.CONFIRMED:
            return "pitch"
        if lead.stage is Stage.CONTACTED and gap is not None and gap >= c.followup_1_after_days + extra:
            return "followup_1"
        if lead.stage is Stage.FOLLOWUP_1 and gap is not None and gap >= c.followup_2_after_days + extra:
            return "followup_2"
        return None

    def _draft(self, lead: Lead, kind: str, ctx: Ctx) -> tuple[Draft, str]:
        c = ctx.cfg
        body, source, note = None, "template", ""
        if ctx.llm is not None:
            lang = c.languages.get(_lang_code(lead), "English")
            lo, hi = WORDS[kind]
            system = (RECONNECT_SYSTEM if kind in ("opener", "opener_nudge") else SALES_SYSTEM).format(
                sender=c.sender_name, owner=c.owner_name, offer=c.offer, lo=lo, hi=hi, language=lang)
            prompt = (f"What we know about them:\n{_facts_block(lead)}\n\n"
                      + TASKS[kind].format(previous=ctx.store.last_sent_text(lead.id) or "(not available)",
                                           reply=ctx.store.last_inbound_text(lead.id) or "(not available)"))
            for _ in range(2):   # one retry for an answer that fails the checks
                try:
                    body = check_message(ctx.llm.complete(system, prompt), c, kind)
                except LLMError as e:
                    note = f"ai_failed: {e}"
                    break
                if body:
                    source = "ai"
                    break
                note = "ai_rejected_by_checks"
        if not body:
            body = _template(lead, kind, ctx)
        text = f"{body}\n\n{c.opt_out_line}" if kind in SELLING else body
        return Draft(lead.id, text, kind, source), note

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        pending = ctx.store.pending_drafts()
        queued = {d.lead_id for _, d in pending}
        per_phone: dict[str, int] = {}
        for _, d in pending:
            a = ctx.store.lead(d.lead_id).account
            per_phone[a] = per_phone.get(a, 0) + 1
        todo: list[tuple[Lead, str]] = []
        for lead in ctx.store.leads():
            if lead.stage not in ACTIVE or lead.id in queued:
                rep.skip("not_active_or_queued")
            elif (kind := self._due(lead, ctx)) is None:
                rep.skip("not_due")
            elif kind != "pitch" and per_phone.get(lead.account, 0) >= ctx.cfg.max_queued_drafts:
                rep.skip("queue_full")   # written later, closer to when it can actually go
            else:
                todo.append((lead, kind))
                per_phone[lead.account] = per_phone.get(lead.account, 0) + 1
        # Drafting is the slow, model-bound part, so it fans out; the store call is locked.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for d, note in pool.map(lambda t: self._draft(*t, ctx), todo):
                ctx.store.add_draft(d)
                rep.done += 1
                if note:
                    rep.skip(note)
        return rep
