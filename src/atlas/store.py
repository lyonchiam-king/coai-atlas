"""SQLite store. One file, no server -- enough for one owner."""
from __future__ import annotations

import sqlite3
import threading
import time

from .models import Draft, Inbound, Lead, Stage

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT UNIQUE, company TEXT DEFAULT '',
  stage TEXT DEFAULT 'new', notes TEXT DEFAULT '', last_contacted REAL);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, text TEXT, kind TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS inbound(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, text TEXT, at REAL, intent TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS sends(id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, at REAL);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        # Agents run on threads; sqlite3 connections are not safe to share unlocked.
        self.lock = threading.RLock()

    def add_lead(self, name: str, phone: str, company: str = "") -> int | None:
        with self.lock:
            try:
                cur = self._db.execute(
                    "INSERT INTO leads(name,phone,company) VALUES(?,?,?)", (name, phone, company))
            except sqlite3.IntegrityError:
                return None   # same number twice is one lead
            self._db.commit()
            return cur.lastrowid

    @staticmethod
    def _lead(r: sqlite3.Row) -> Lead:
        return Lead(r["id"], r["name"], r["phone"], Stage(r["stage"]), r["company"],
                    r["notes"], r["last_contacted"])

    def leads(self, *stages: Stage) -> list[Lead]:
        with self.lock:
            rows = self._db.execute("SELECT * FROM leads ORDER BY id").fetchall()
        out = [self._lead(r) for r in rows]
        return [l for l in out if not stages or l.stage in stages]

    def lead(self, lead_id: int) -> Lead:
        with self.lock:
            return self._lead(self._db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())

    def set_stage(self, lead_id: int, stage: Stage) -> None:
        with self.lock:
            self._db.execute("UPDATE leads SET stage=? WHERE id=?", (stage.value, lead_id))
            self._db.commit()

    def mark_sent(self, lead_id: int, stage: Stage, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            self._db.execute("UPDATE leads SET stage=?, last_contacted=? WHERE id=?",
                             (stage.value, now, lead_id))
            self._db.execute("INSERT INTO sends(lead_id, at) VALUES(?,?)", (lead_id, now))
            self._db.commit()

    def sends_since(self, since: float) -> int:
        with self.lock:
            return self._db.execute("SELECT COUNT(*) FROM sends WHERE at>=?", (since,)).fetchone()[0]

    def add_draft(self, d: Draft) -> None:
        with self.lock:
            self._db.execute("INSERT INTO drafts(lead_id,text,kind) VALUES(?,?,?)",
                             (d.lead_id, d.text, d.kind))
            self._db.commit()

    def pending_drafts(self) -> list[tuple[int, Draft]]:
        with self.lock:
            rows = self._db.execute("SELECT * FROM drafts WHERE sent=0 ORDER BY id").fetchall()
        return [(r["id"], Draft(r["lead_id"], r["text"], r["kind"])) for r in rows]

    def close_draft(self, draft_id: int) -> None:
        with self.lock:
            self._db.execute("UPDATE drafts SET sent=1 WHERE id=?", (draft_id,))
            self._db.commit()

    def drop_drafts(self, lead_id: int) -> None:
        with self.lock:
            self._db.execute("UPDATE drafts SET sent=1 WHERE lead_id=? AND sent=0", (lead_id,))
            self._db.commit()

    def add_inbound(self, i: Inbound) -> None:
        with self.lock:
            self._db.execute("INSERT INTO inbound(lead_id,text,at) VALUES(?,?,?)",
                             (i.lead_id, i.text, i.at))
            self._db.commit()

    def unclassified(self) -> list[tuple[int, Inbound]]:
        with self.lock:
            rows = self._db.execute("SELECT * FROM inbound WHERE intent='' ORDER BY id").fetchall()
        return [(r["id"], Inbound(r["lead_id"], r["text"], r["at"])) for r in rows]

    def set_intent(self, inbound_id: int, intent: str) -> None:
        with self.lock:
            self._db.execute("UPDATE inbound SET intent=? WHERE id=?", (intent, inbound_id))
            self._db.commit()
