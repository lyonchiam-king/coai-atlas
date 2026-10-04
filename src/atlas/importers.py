"""Turn whatever list the owner has into rows of name / phone / company / facts.

Three shapes arrive in practice:
  - a vCard (.vcf) exported from a phone's contacts app
  - a CSV: Google Contacts' export, or a spreadsheet typed by hand
  - the phone's own WhatsApp chats, gathered by the relay

Every unrecognised column is kept as a fact for the writer. Columns that are
only bookkeeping in Google's export ("Phone 1 - Label", "Photo") are dropped,
because "Photo: https://..." is not something to write a message from.
"""
from __future__ import annotations

import csv
import io
import quopri
import re
from datetime import datetime, timezone

NAME = ("name", "full name", "display name", "nama", "contact name")
FIRST = ("first name", "given name")
MIDDLE = ("middle name", "additional name")
LAST = ("last name", "family name")
PHONE = ("phone", "phone number", "mobile", "mobile phone", "whatsapp", "tel", "telephone", "hp",
         "no tel", "no. tel", "handphone", "contact", "contact number", "phone 1 - value", "number")
COMPANY = ("company", "organization", "organisation", "organization name", "organization 1 - name",
           "business", "syarikat", "shop")
NOTES = ("notes", "note", "catatan", "remarks")
LABELS = ("labels", "group membership")
JUNK = re.compile(r"^(phone|e-?mail|address|website|im|relation|event|custom field) \d+ - (type|label)$"
                  r"|^(photo|file as|name prefix|name suffix|phonetic .*|yomi .*|.* - type)$")
MAX_FACTS, MAX_LEN = 12, 300


def _pick(row: dict, keys) -> str:
    for k in keys:
        v = row.get(k, "")
        if v and v.strip():
            return v.strip()
    return ""


def _first_phone(value: str) -> str:
    # Google joins several numbers in one cell with " ::: ".
    return value.split(":::")[0].strip()


def _row_from_mapping(raw: dict) -> dict | None:
    row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
    name = _pick(row, NAME) or " ".join(x for x in (_pick(row, FIRST), _pick(row, MIDDLE), _pick(row, LAST)) if x)
    phone = _first_phone(_pick(row, PHONE))
    if not phone:
        # Any other column that holds a phone number, e.g. "Phone 2 - Value".
        phone = next((_first_phone(v) for k, v in row.items() if "phone" in k and "value" in k and v), "")
    if not phone:
        return None
    used = set(NAME + FIRST + MIDDLE + LAST + PHONE + COMPANY)
    facts = {}
    notes = _pick(row, NOTES)
    if notes:
        facts["notes"] = notes[:MAX_LEN]
    labels = _pick(row, LABELS).replace("* myContacts", "").replace(":::", ",").strip(" ,")
    if labels:
        facts["labels"] = labels[:MAX_LEN]
    for k, v in row.items():
        if len(facts) >= MAX_FACTS:
            break
        if v and k not in used and k not in NOTES + LABELS and not JUNK.match(k) and "phone" not in k:
            facts[k.replace(" - value", "").replace(" ", "_")] = v[:MAX_LEN]
    return {"name": name, "phone": phone, "company": _pick(row, COMPANY), "facts": facts}


def parse_csv(text: str) -> list[dict]:
    text = text.lstrip("﻿").strip()
    try:
        dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [_row_from_mapping(r) for r in csv.DictReader(io.StringIO(text), dialect=dialect)]
    return [r for r in rows if r]


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]                      # RFC 6350 folding
        elif lines and lines[-1].endswith("=") and "QUOTED-PRINTABLE" in lines[-1].upper():
            lines[-1] = lines[-1][:-1] + raw          # quoted-printable soft line break
        else:
            lines.append(raw)
    return lines


def _vvalue(params: str, value: str) -> str:
    if "QUOTED-PRINTABLE" in params.upper():
        charset = re.search(r"CHARSET=([\w-]+)", params, re.I)
        value = quopri.decodestring(value.encode()).decode(charset.group(1) if charset else "utf-8", "replace")
    return value.replace("\\n", " ").replace("\\,", ",").replace("\;", ";").strip()


def parse_vcf(text: str) -> list[dict]:
    out = []
    card: dict | None = None
    for line in _unfold(text):
        if line.upper().startswith("BEGIN:VCARD"):
            card = {"tels": [], "facts": {}}
            continue
        if card is None or ":" not in line:
            continue
        if line.upper().startswith("END:VCARD"):
            tels = sorted(card["tels"], key=lambda t: 0 if re.search(r"CELL|MOBILE|WHATSAPP", t[0], re.I) else 1)
            name = card.get("fn") or card.get("n", "")
            if tels:
                out.append({"name": name, "phone": tels[0][1], "company": card.get("org", ""),
                            "facts": card["facts"]})
            card = None
            continue
        head, value = line.split(":", 1)
        prop, _, params = head.partition(";")
        prop = prop.split(".")[-1].upper()           # "item1.TEL" -> "TEL"
        value = _vvalue(params, value)
        if not value:
            continue
        if prop == "FN":
            card["fn"] = value
        elif prop == "N":
            parts = [p for p in value.split(";") if p.strip()]
            card["n"] = " ".join(reversed(parts[:2])) if len(parts) >= 2 else " ".join(parts)
        elif prop == "TEL":
            card["tels"].append((params, value))
        elif prop == "ORG":
            card["org"] = value.split(";")[0]
        elif prop == "NOTE":
            card["facts"]["notes"] = value[:MAX_LEN]
        elif prop == "TITLE":
            card["facts"]["job_title"] = value[:MAX_LEN]
        elif prop == "NICKNAME":
            card["facts"]["nickname"] = value[:MAX_LEN]
        elif prop == "BDAY":
            card["facts"]["birthday"] = value[:MAX_LEN]
    return out


def parse_contacts(text: str) -> list[dict]:
    """Whatever was pasted or uploaded: vCard or CSV, decided by looking at it."""
    if "BEGIN:VCARD" in text[:2000].upper():
        return parse_vcf(text)
    return parse_csv(text)


def from_whatsapp(contacts: list[dict]) -> list[dict]:
    """The relay's contact book -> rows. Skips anyone with no name at all:
    an unnamed number from a group chat is not someone the owner knows."""
    rows = []
    for c in contacts:
        name = (c.get("name") or c.get("notify") or "").strip()
        if not name or not c.get("phone"):
            continue
        facts = {}
        if c.get("notify") and c.get("notify") != name:
            facts["whatsapp_name"] = c["notify"]
        if c.get("last_chat"):
            facts["last_chatted"] = datetime.fromtimestamp(int(c["last_chat"]), timezone.utc).strftime("%B %Y")
        rows.append({"name": name, "phone": c["phone"], "company": "", "facts": facts})
    return rows
