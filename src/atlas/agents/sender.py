from __future__ import annotations

from ..channels import Channel
from ..channels.relay import RelayError
from ..guard import may_send
from ..models import Report, Stage
from . import Ctx

NEXT = {"first": Stage.CONTACTED, "followup_1": Stage.FOLLOWUP_1, "followup_2": Stage.FOLLOWUP_2}


class Sender:
    name = "sender"

    def __init__(self, channel: Channel):
        self.channel = channel

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        for draft_id, d in ctx.store.pending_drafts():
            # Re-read the lead: a STOP may have landed since the draft was written.
            lead = ctx.store.lead(d.lead_id)
            ok, why = may_send(lead, ctx.store, ctx.cfg, ctx.now())
            if not ok:
                rep.skip(why)
                if why.startswith("stage_"):
                    ctx.store.close_draft(draft_id)
                if why == "daily_cap":
                    break
                continue
            try:
                self.channel.send(lead.phone, d.text)
            except Exception as e:   # one bad send must not stop the queue
                rep.skip(f"send_failed: {e}" if isinstance(e, RelayError) else f"send_failed:{type(e).__name__}")
                if getattr(e, "stop_batch", False):
                    break
                continue
            ctx.store.mark_sent(lead.id, NEXT[d.kind], ctx.now(), d.text)
            ctx.store.close_draft(draft_id)
            rep.done += 1
        return rep
