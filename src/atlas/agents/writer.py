from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..models import ACTIVE, Draft, Lead, Report, Stage
from . import Ctx

DAY = 86400

SYSTEM = ("You write short, warm WhatsApp messages for a Malaysian SME founder. "
          "No hype, no emoji spam, under 60 words. Never mention opt-out; it is added for you.")


class Writer:
    name = "writer"

    def _due(self, lead: Lead, ctx: Ctx) -> str | None:
        gap = (ctx.now() - lead.last_contacted) / DAY if lead.last_contacted else None
        if lead.stage is Stage.NEW:
            return "first"
        if lead.stage is Stage.CONTACTED and gap is not None and gap >= ctx.cfg.followup_1_after_days:
            return "followup_1"
        if lead.stage is Stage.FOLLOWUP_1 and gap is not None and gap >= ctx.cfg.followup_2_after_days:
            return "followup_2"
        return None

    def _draft(self, lead: Lead, kind: str, ctx: Ctx) -> Draft:
        c = ctx.cfg
        brief = {
            "first": f"Hi {lead.name}, {c.sender_name}. We build {c.offer}. Worth a quick chat?",
            "followup_1": f"Hi {lead.name}, following up on my last note about {c.offer}. Still keen?",
            "followup_2": f"Hi {lead.name}, last nudge from me. Happy to leave it here if now isn't the time.",
        }[kind]
        body = ctx.llm.complete(SYSTEM, brief).strip()
        return Draft(lead.id, f"{body}\n\n{c.opt_out_line}", kind)

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        queued = {d.lead_id for _, d in ctx.store.pending_drafts()}
        todo: list[tuple[Lead, str]] = []
        for lead in ctx.store.leads():
            if lead.stage not in ACTIVE or lead.id in queued:
                rep.skip("not_active_or_queued")
            elif (kind := self._due(lead, ctx)) is None:
                rep.skip("not_due")
            else:
                todo.append((lead, kind))
        # Drafting is the slow, model-bound part, so it fans out; the store call is locked.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for d in pool.map(lambda t: self._draft(*t, ctx), todo):
                ctx.store.add_draft(d)
                rep.done += 1
        return rep
