"""Runs first every tick, so a reply or a STOP always beats a queued follow-up."""
from __future__ import annotations

from ..guard import is_opt_out
from ..models import Report, Stage
from . import Ctx


class Responder:
    name = "responder"

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        for inbound_id, msg in ctx.store.unclassified():
            # Opt-out is a keyword rule on purpose: never leave "did they say stop" to a model.
            intent = "opt_out" if is_opt_out(msg.text) else "reply"
            ctx.store.set_intent(inbound_id, intent)
            ctx.store.set_stage(msg.lead_id, Stage.DO_NOT_CONTACT if intent == "opt_out" else Stage.REPLIED)
            ctx.store.drop_drafts(msg.lead_id)
            rep.done += 1
        return rep
