"""Asks WhatsApp whether each new number is still on it, and reads the free profile.

Long-lost lists are full of numbers that have gone dead. Writing to one costs a
send from the day's quota and, worse, a message WhatsApp accepts and delivers
to nobody. A "no" here retires the lead before anything is written.

For a "yes" it also reads what the person shows publicly on WhatsApp -- their
About line, and for a WhatsApp Business account its category, description and
website. That is the free research: the writer gets it as facts.
"""
from __future__ import annotations

from datetime import datetime

from ..models import Report, Stage
from . import Ctx

PROFILE_FIELDS = {"about": "wa_about", "category": "wa_business_category",
                  "description": "wa_business_description", "website": "wa_website",
                  "address": "wa_business_address"}


class Checker:
    def __init__(self, relay, account: str = "1"):
        self.relay = relay
        self.account = account
        self.name = f"checker {account}"

    def _budget_left(self, ctx: Ctx, local: datetime) -> tuple[str, int]:
        key = f"checks:{self.account}:{local.date().isoformat()}"
        used = int(ctx.store.get(key) or 0)
        return key, ctx.cfg.checks_per_day - used

    def run(self, ctx: Ctx) -> Report:
        rep = Report(self.name)
        local = datetime.fromtimestamp(ctx.now(), ctx.cfg.tz)
        # Lookups are activity too: only in the hours a person would be on their phone.
        if not ctx.cfg.send_from_hour <= local.hour < ctx.cfg.send_until_hour:
            rep.skip("quiet_hours")
            return rep
        key, left = self._budget_left(ctx, local)
        todo = [l for l in ctx.store.leads(Stage.NEW, account=self.account) if not l.wa]
        todo = todo[:max(0, min(ctx.cfg.checks_per_run, left))]
        if not todo:
            if left <= 0:
                rep.skip("daily_check_budget_used")
            return rep
        try:
            results = self.relay.check([l.phone for l in todo])
        except Exception as e:
            rep.skip(f"check_failed: {e}")
            return rep
        ctx.store.put(key, str(ctx.cfg.checks_per_day - left + len(todo)))
        for lead in todo:
            verdict = (results.get(lead.phone) or {}).get("registered")
            if verdict is True:
                ctx.store.set_wa(lead.id, "yes")
                rep.done += 1
                self._research(ctx, lead.id, lead.phone, rep)
            elif verdict is False:
                ctx.store.set_wa(lead.id, "no")
                ctx.store.set_stage(lead.id, Stage.NOT_ON_WHATSAPP)
                rep.skip("not_on_whatsapp")
            else:
                rep.skip("check_unknown")   # a hiccup is not a "no"; asked again next run
        return rep

    def _research(self, ctx: Ctx, lead_id: int, phone: str, rep: Report) -> None:
        try:
            prof = self.relay.profile(phone)
        except Exception:
            rep.skip("profile_unavailable")   # research is a bonus, never a blocker
            return
        flat = {"about": prof.get("about", ""), **(prof.get("business") or {})}
        facts = {PROFILE_FIELDS[k]: str(v).strip()[:300] for k, v in flat.items()
                 if k in PROFILE_FIELDS and v and str(v).strip()}
        if facts:
            ctx.store.add_facts(lead_id, facts)
