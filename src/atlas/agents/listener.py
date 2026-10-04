"""Pulls replies from the relay into the store. Runs before the Responder."""
from __future__ import annotations

from ..models import Inbound, Report
from . import Ctx


class Listener:
    def __init__(self, source, account: str = "1"):
        self.source = source   # anything with poll() and ack(ids)
        self.name = f"listener {account}"

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        seen: list[str] = []
        for m in self.source.poll():
            # Stored whichever phone it reached: a STOP sent to the "wrong" phone of
            # ours is still a STOP.
            lead = ctx.store.lead_by_phone(m["phone"])
            if lead is None:
                rep.skip("unknown_sender")   # not one of ours; acked so it is not re-read
            elif ctx.store.add_inbound(Inbound(lead.id, m["text"], m["at"]), m["id"]):
                rep.done += 1
            else:
                rep.skip("already_stored")
            seen.append(m["id"])
        # Ack only after everything is stored: a crash above re-delivers, never loses a STOP.
        if seen:
            self.source.ack(seen)
        return rep
