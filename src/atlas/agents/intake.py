from __future__ import annotations

from ..importers import parse_contacts
from ..models import Report
from ..phone import to_e164
from . import Ctx


class Intake:
    """Rows from any importer -> leads on one phone, in one named list."""

    name = "intake"

    def run(self, ctx: Ctx, path: str, account: str = "1", list_name: str = "") -> Report:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return self.run_text(ctx, f.read(), account, list_name)

    def run_text(self, ctx: Ctx, text: str, account: str = "1", list_name: str = "") -> Report:
        """A pasted or uploaded file: vCard or CSV."""
        return self.run_rows(ctx, parse_contacts(text), account, list_name)

    def run_rows(self, ctx: Ctx, rows, account: str = "1", list_name: str = "") -> Report:
        rep = Report(self.name)
        for row in rows:
            phone = to_e164(row.get("phone", ""), ctx.cfg.country)
            if not phone:
                rep.skip("bad_phone")
            elif ctx.store.add_lead(row.get("name", "").strip(), phone, row.get("company", "").strip(),
                                    row.get("facts") or {}, account, list_name) is None:
                # Already on a list -- possibly another phone's. It stays where it is,
                # so nobody hears from two of your phones.
                rep.skip("duplicate")
            else:
                rep.done += 1
        return rep
