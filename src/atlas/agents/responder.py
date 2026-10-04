"""Runs first every tick, so a reply or a STOP always beats a queued message.

A reply to "is this still your number?" is read three ways:

    confirm  it's them         -> CONFIRMED; the writer answers with the pitch
    wrong    someone else now  -> WRONG_NUMBER; never written to again
    unsure   "who is this?"    -> REPLIED; a person takes over

Any other reply hands the conversation to the owner. When in doubt the answer
is always "a person reads it": an AI pitching someone who asked "who is this?"
is exactly the message that gets a number reported.
"""
from __future__ import annotations

import re

from ..guard import is_opt_out
from ..llm import LLMError
from ..models import AWAITING_CONFIRM, Report, Stage
from . import Ctx

WRONG = ("wrong number", "wrong no", "salah nombor", "salah number", "salah no", "bukan saya",
         "not me", "no such person", "don't know you", "dont know you", "tak kenal", "打错", "不是我",
         "不认识", "you have the wrong")
WHO = re.compile(r"\b(who|siapa|sapa)\b|谁|哪位")
CONFIRM = re.compile(
    # A bare "hi" or "hello" is not here on purpose: a stranger says that too.
    r"^\s*(yes|ya+|yah|yup|yeah|yep|yea|betul|ye+|haa|ha'?ah|correct|it'?s me|speaking|saya|yes it is)\b"
    r"|long time|lama tak|好久不见|是我|是的|对|还是我|我是")

CLASSIFY_SYSTEM = """Someone was sent: "Hi, is this still your number? It's {owner} here."
Classify their reply into exactly one word:
CONFIRM - it is clearly still them (they greet back, say yes, or engage as if they know {owner})
WRONG - the number now belongs to someone else
UNSURE - anything else, including asking who this is
Reply with the one word only."""


def classify_confirm(text: str, ctx: Ctx) -> str:
    t = text.lower().strip()
    if any(w in t for w in WRONG):
        return "wrong"
    if WHO.search(t):
        return "unsure"       # "yes, who is this?" is not a confirmation
    if CONFIRM.search(t) or ctx.cfg.owner_name.lower() in t:
        return "confirm"
    if ctx.llm is not None:
        try:
            word = ctx.llm.complete(CLASSIFY_SYSTEM.format(owner=ctx.cfg.owner_name), text)
        except LLMError:
            return "unsure"
        first = (word.strip().split() or [""])[0].strip(".:").upper()
        return {"CONFIRM": "confirm", "WRONG": "wrong"}.get(first, "unsure")
    return "unsure"


class Responder:
    name = "responder"

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        for inbound_id, msg in ctx.store.unclassified():
            lead = ctx.store.lead(msg.lead_id)
            # Opt-out is a keyword rule on purpose: never leave "did they say stop" to a model.
            if is_opt_out(msg.text):
                intent, stage = "opt_out", Stage.DO_NOT_CONTACT
            elif lead.stage in AWAITING_CONFIRM:
                intent = classify_confirm(msg.text, ctx)
                stage = {"confirm": Stage.CONFIRMED, "wrong": Stage.WRONG_NUMBER}.get(intent, Stage.REPLIED)
            elif lead.stage is Stage.CONFIRMED:
                # A second message before our answer went out: still waiting on us, keep the pitch.
                intent, stage = "reply", Stage.CONFIRMED
            else:
                intent, stage = "reply", Stage.REPLIED
            ctx.store.set_intent(inbound_id, intent)
            if stage is not Stage.CONFIRMED or lead.stage is not Stage.CONFIRMED:
                ctx.store.drop_drafts(msg.lead_id)
            ctx.store.set_stage(msg.lead_id, stage)
            rep.done += 1
            rep.skip(intent)
        return rep
