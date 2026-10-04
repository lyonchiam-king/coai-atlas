from __future__ import annotations

import csv
import io

from ..models import Report
from ..phone import to_e164
from . import Ctx


class Intake:
    name = "intake"

    def run(self, ctx: Ctx, csv_path: str) -> Report:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            return self.run_rows(ctx, csv.DictReader(f))

    def run_text(self, ctx: Ctx, text: str) -> Report:
        """CSV pasted into the control page."""
        return self.run_rows(ctx, csv.DictReader(io.StringIO(text.lstrip("\ufeff").strip())))

    def run_rows(self, ctx: Ctx, rows) -> Report:
        rep = Report(self.name)
        for row in rows:
            # Headers typed by hand in Excel arrive as "Phone " or "NAME".
            row = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
            phone = to_e164(row.get("phone", ""), ctx.cfg.country)
            if not phone:
                rep.skip("bad_phone")
            elif ctx.store.add_lead(row.get("name", "").strip(), phone, row.get("company", "").strip()) is None:
                rep.skip("duplicate")
            else:
                rep.done += 1
        return rep
