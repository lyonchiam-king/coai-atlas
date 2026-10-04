"""SQLite store. One file, no server -- enough for one owner."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from .models import Draft, Inbound, Lead, Stage

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT UNIQUE, company TEXT DEFAULT '',
  stage TEXT DEFAULT 'new', notes TEXT DEFAULT '', last_contacted REAL, facts TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, text TEXT, kind TEXT, sent INTEGER DEFAULT 0,
  source TEXT DEFAULT 'template');
CREATE TABLE IF NOT EXISTS inbound(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, text TEXT, at REAL, intent TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS sends(id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, at REAL, text TEXT DEFAULT '',
  source TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""

# Columns added after the first release. An atlas.db made by an older version
# lacks them, and CREATE TABLE IF NOT EXISTS never alters an existing table.
MIGRATIONS = [
    ("leads", "facts", "TEXT DEFAULT '{}'"),
    ("sends", "text", "TEXT DEFAULT ''"),
    ("sends", "source", "TEXT DEFAULT ''"),
    ("inbound", "ext_id", "TEXT"),
    ("drafts", "source", "TEXT DEFAULT 'template'"),
]


class Store:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        # ext_id must be unique for re-delivery dedupe; an index works on old tables too.
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS inbound_ext ON inbound(ext_id)")
        self._db.commit()
        # Agents run on threads; sqlite3 connections are not safe to share unlocked.
        self.lock = threading.RLock()

    def add_lead(self, name: str, phone: str, company: str = "", facts: dict | None = None) -> int | None:
        with self.lock:
            try:
                cur = self._db.execute(
                    "INSERT INTO leads(name,phone,company,facts) VALUES(?,?,?,?)",
                    (name, phone, company, json.dumps(facts or {}, ensure_ascii=False)))
            except sqlite3.IntegrityError:
                return None   # same number twice is one lead
            self._db.commit()
            return cur.lastrowid

    @staticmethod
    def _lead(r: sqlite3.Row) -> Lead:
        return Lead(r["id"], r["name"], r["phone"], Stage(r["stage"]), r["company"],
                    r["notes"], r["last_contacted"], json.loads(r["facts"] or "{}"))

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

    def mark_sent(self, lead_id: int, stage: Stage, now: float | None = None, text: str = "",
                  source: str = "") -> None:
        now = time.time() if now is None else now
        with self.lock:
            self._db.execute("UPDATE leads SET stage=?, last_contacted=? WHERE id=?",
                             (stage.value, now, lead_id))
            self._db.execute("INSERT INTO sends(lead_id, at, text, source) VALUES(?,?,?,?)",
                             (lead_id, now, text, source))
            self._db.commit()

    def sends_since(self, since: float) -> int:
        with self.lock:
            return self._db.execute("SELECT COUNT(*) FROM sends WHERE at>=?", (since,)).fetchone()[0]

    def stage_counts(self) -> dict[str, int]:
        with self.lock:
            rows = self._db.execute("SELECT stage, COUNT(*) FROM leads GROUP BY stage").fetchall()
        return {r[0]: r[1] for r in rows}

    def activity(self, limit: int = 30) -> list[dict]:
        """Recent sends and replies, newest first, for the control page."""
        q = """
          SELECT 'out' AS dir, s.at, s.text, l.name, l.phone, '' AS intent, s.source FROM sends s
            JOIN leads l ON l.id=s.lead_id
          UNION ALL
          SELECT 'in', i.at, i.text, l.name, l.phone, i.intent, '' FROM inbound i JOIN leads l ON l.id=i.lead_id
          ORDER BY at DESC LIMIT ?"""
        with self.lock:
            return [dict(r) for r in self._db.execute(q, (limit,)).fetchall()]

    def add_draft(self, d: Draft) -> None:
        with self.lock:
            self._db.execute("INSERT INTO drafts(lead_id,text,kind,source) VALUES(?,?,?,?)",
                             (d.lead_id, d.text, d.kind, d.source))
            self._db.commit()

    def pending_drafts(self) -> list[tuple[int, Draft]]:
        with self.lock:
            rows = self._db.execute("SELECT * FROM drafts WHERE sent=0 ORDER BY id").fetchall()
        return [(r["id"], Draft(r["lead_id"], r["text"], r["kind"], r["source"])) for r in rows]

    def queue_view(self, limit: int = 20) -> list[dict]:
        """Drafts waiting to go, with who they are for -- what the owner reviews."""
        q = """SELECT d.id, d.kind, d.text, d.source, l.name, l.company, l.phone
               FROM drafts d JOIN leads l ON l.id=d.lead_id WHERE d.sent=0 ORDER BY d.id LIMIT ?"""
        with self.lock:
            return [dict(r) for r in self._db.execute(q, (limit,)).fetchall()]

    def last_sent_text(self, lead_id: int) -> str:
        with self.lock:
            r = self._db.execute("SELECT text FROM sends WHERE lead_id=? ORDER BY at DESC LIMIT 1",
                                 (lead_id,)).fetchone()
        return r[0] if r else ""

    def get(self, key: str) -> str | None:
        with self.lock:
            r = self._db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return r[0] if r else None

    def put(self, key: str, value: str) -> None:
        with self.lock:
            self._db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                             (key, value))
            self._db.commit()

    def close_draft(self, draft_id: int) -> None:
        with self.lock:
            self._db.execute("UPDATE drafts SET sent=1 WHERE id=?", (draft_id,))
            self._db.commit()

    def drop_drafts(self, lead_id: int) -> None:
        with self.lock:
            self._db.execute("UPDATE drafts SET sent=1 WHERE lead_id=? AND sent=0", (lead_id,))
            self._db.commit()

    def lead_by_phone(self, phone: str) -> Lead | None:
        with self.lock:
            r = self._db.execute("SELECT * FROM leads WHERE phone=?", (phone,)).fetchone()
        return self._lead(r) if r else None

    def add_inbound(self, i: Inbound, ext_id: str | None = None) -> bool:
        """False if this exact WhatsApp message was already stored (relay re-delivery)."""
        with self.lock:
            try:
                self._db.execute("INSERT INTO inbound(lead_id,text,at,ext_id) VALUES(?,?,?,?)",
                                 (i.lead_id, i.text, i.at, ext_id))
            except sqlite3.IntegrityError:
                return False
            self._db.commit()
            return True

    def unclassified(self) -> list[tuple[int, Inbound]]:
        with self.lock:
            rows = self._db.execute("SELECT * FROM inbound WHERE intent='' ORDER BY id").fetchall()
        return [(r["id"], Inbound(r["lead_id"], r["text"], r["at"])) for r in rows]

    def set_intent(self, inbound_id: int, intent: str) -> None:
        with self.lock:
            self._db.execute("UPDATE inbound SET intent=? WHERE id=?", (intent, inbound_id))
            self._db.commit()
