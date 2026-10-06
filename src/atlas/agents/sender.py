"""Sends at most one message per run, when the pacer says a person would.

The guard decides whether a message may go at all; the pacer decides whether
now is the moment. Both are asked every time.
"""
from __future__ import annotations

from ..channels import Channel
from ..channels.relay import RelayError
from ..guard import may_send
from ..models import Report, Stage
from ..pacing import Pacer
from . import Ctx

NEXT = {"opener": Stage.OPENER_SENT, "opener_nudge": Stage.OPENER_NUDGED, "pitch": Stage.CONTACTED,
        "first": Stage.CONTACTED, "followup_1": Stage.FOLLOWUP_1, "followup_2": Stage.FOLLOWUP_2}


class Sender:
    """One per phone. Sends only this phone's leads, through this phone's relay."""

    def __init__(self, channel: Channel, pacer: Pacer | None = None, account: str = "1"):
        self.channel = channel
        self.pacer = pacer
        self.account = account
        self.name = f"sender {account}"

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        pacer = self.pacer or Pacer(ctx.store, ctx.cfg, ctx.rng, self.account)
        for draft_id, d in ctx.store.pending_drafts(self.account):
            now = ctx.now()
            ready, why_not = pacer.ready(now, priority=d.kind == "pitch")
            if not ready:
                rep.skip(f"pacing: {why_not}")
                break
            # Re-read the lead: a STOP may have landed since the draft was written.
            lead = ctx.store.lead(d.lead_id)
            ok, why = may_send(lead, ctx.store, ctx.cfg, now)
            if not ok:
                rep.skip(why)
                if why.startswith("stage_"):
                    ctx.store.close_draft(draft_id)
                    continue          # a dead draft costs no pacing; look at the next
                break                 # quiet hours or cap: nothing else can go either
            typing = pacer.typing_ms(d.text) if ctx.cfg.human_pacing else 0
            try:
                if d.media:
                    self.channel.send(lead.phone, d.text, typing_ms=typing, media=d.media)
                else:
                    self.channel.send(lead.phone, d.text, typing_ms=typing)
            except Exception as e:   # one bad send must not stop the queue
                rep.skip(f"send_failed: {e}" if isinstance(e, RelayError) else f"send_failed:{type(e).__name__}")
                if getattr(e, "stop_batch", False):
                    break
                continue
            ctx.store.mark_sent(lead.id, NEXT[d.kind], now, d.text, d.source, d.media)
            ctx.store.close_draft(draft_id)
            rep.done += 1
            if ctx.cfg.human_pacing:
                pacer.after_send(now)
                break                 # one per run; the next waits for its gap
        return rep
