"""The video and picture library, and which one goes with which message.

Files live in one folder (media/, beside atlas.db). The relay reads them from
that folder itself, by bare file name: it never accepts a path, so nothing
outside the folder can ever be sent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

KINDS = {".mp4": "video", ".jpg": "image", ".jpeg": "image", ".png": "image"}
MAX_BYTES = 64 * 1024 * 1024
BIG_FOR_WHATSAPP = 16 * 1024 * 1024


def safe_name(name: str) -> str | None:
    """A plain file name we accept, or None. No folders, no odd characters."""
    base = Path(name.replace("\\", "/")).name
    stem, ext = Path(base).stem, Path(base).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "", stem).strip(" .")[:80]
    if ext not in KINDS or not stem:
        return None
    return stem + ext


class Library:
    def __init__(self, folder: str | Path, store):
        self.folder, self.store = Path(folder), store

    def _off(self) -> set[str]:
        try:
            return set(json.loads(self.store.get("media_off") or "[]"))
        except ValueError:
            return set()

    def list(self) -> list[dict]:
        if not self.folder.is_dir():
            return []
        off = self._off()
        out = []
        for p in sorted(self.folder.iterdir()):
            if p.is_file() and p.suffix.lower() in KINDS:
                size = p.stat().st_size
                out.append({"name": p.name, "kind": KINDS[p.suffix.lower()], "size": size,
                            "on": p.name not in off, "big": size > BIG_FOR_WHATSAPP})
        return out

    def save(self, name: str, data: bytes) -> dict:
        clean = safe_name(name)
        if clean is None:
            return {"error": "Use .mp4 for videos, or .jpg / .png for pictures."}
        if len(data) > MAX_BYTES:
            return {"error": "That file is over 64 MB. Shorten or compress the video first."}
        if not data:
            return {"error": "That file is empty."}
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / clean
        n = 2
        while target.exists():               # never overwrite a file that may be queued
            target = self.folder / f"{Path(clean).stem} ({n}){Path(clean).suffix}"
            n += 1
        target.write_bytes(data)
        return {"ok": True, "name": target.name, "big": len(data) > BIG_FOR_WHATSAPP}

    def set_on(self, name: str, on: bool) -> dict:
        off = self._off()
        (off.discard if on else off.add)(name)
        self.store.put("media_off", json.dumps(sorted(off)))
        return {"ok": True}

    def delete(self, name: str) -> dict:
        clean = safe_name(name)
        p = self.folder / clean if clean else None
        if not p or not p.is_file():
            return {"error": "No such file."}
        p.unlink()
        return {"ok": True}

    def pick(self, lead_id: int, rng) -> str:
        """A random file in rotation, preferring one this person has not had yet."""
        pool = [m["name"] for m in self.list() if m["on"]]
        if not pool:
            return ""
        seen = set(self.store.media_used_for(lead_id))
        fresh = [n for n in pool if n not in seen]
        return rng.choice(fresh or pool)
