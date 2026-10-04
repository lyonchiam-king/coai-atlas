from __future__ import annotations

import argparse
import json
import time

from .agents import Ctx
from .agents.intake import Intake
from .channels.relay import RelayChannel
from .config import Config
from .llm import make_writer
from .store import Store
from .swarm import Swarm


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="atlas")
    p.add_argument("--db", default="atlas.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="load contacts from a .csv or .vcf onto one phone's list")
    imp.add_argument("file")
    imp.add_argument("--phone", default="1")
    imp.add_argument("--list", default="")
    sub.add_parser("tick", help="run the swarm once")
    srv = sub.add_parser("serve", help="open the control page")
    srv.add_argument("--port", type=int, default=8790)
    srv.add_argument("--no-browser", action="store_true")
    a = p.parse_args(argv)
    store = Store(a.db)
    cfg = Config()
    if a.cmd == "import":
        r = Intake().run(Ctx(store, cfg, None, time.time), a.file, a.phone, a.list)
        print(f"imported {r.done}, skipped {r.skipped}")
        return
    # One relay per phone; Atlas.bat starts them on these ports.
    channels = {acc: RelayChannel(url) for acc, _, url in cfg.accounts}
    # Which AI writes is a page setting; the key stays in .env and is never printed.
    llm = make_writer(json.loads(store.get("writer_settings") or "{}"))
    swarm = Swarm(store, cfg=cfg, llm=llm, channels=channels)
    if a.cmd == "tick":
        for r in swarm.tick():
            print(f"{r.agent}: done={r.done} skipped={r.skipped}")
    else:
        from .web import Control, serve
        print("Messages written by:", llm.name if llm else "templates (set a writer on the page)")
        serve(Control(swarm), a.port, not a.no_browser)
