from __future__ import annotations

import csv

from ..models import Report
from ..phone import to_e164
from . import Ctx


class Intake:
    name = "intake"

    def run(self, ctx: Ctx, csv_path: str) -> Report:
        rep = Report(self.name)
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                phone = to_e164(row.get("phone", ""), ctx.cfg.country)
                if not phone:
                    rep.skip("bad_phone")
                elif ctx.store.add_lead(row.get("name", "").strip(), phone, row.get("company", "").strip()) is None:
                    rep.skip("duplicate")
                else:
                    rep.done += 1
        return rep
