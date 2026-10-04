from __future__ import annotations

import argparse
import time

from .agents import Ctx
from .agents.intake import Intake
from .channels.relay import RelayChannel
from .config import Config
from .llm import TemplateLLM
from .store import Store
from .swarm import Swarm


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="atlas")
    p.add_argument("--db", default="atlas.db")
    p.add_argument("--relay", default="http://127.0.0.1:3001")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="load leads from a CSV with name,phone,company")
    imp.add_argument("csv")
    sub.add_parser("tick", help="run the swarm once")
    srv = sub.add_parser("serve", help="open the control page")
    srv.add_argument("--port", type=int, default=8790)
    srv.add_argument("--no-browser", action="store_true")
    a = p.parse_args(argv)
    store = Store(a.db)
    relay = RelayChannel(a.relay)
    if a.cmd == "import":
        r = Intake().run(Ctx(store, Config(), TemplateLLM(), time.time), a.csv)
        print(f"imported {r.done}, skipped {r.skipped}")
    elif a.cmd == "tick":
        for r in Swarm(store, relay, inbox=relay).tick():
            print(f"{r.agent}: done={r.done} skipped={r.skipped}")
    else:
        from .web import Control, serve
        serve(Control(Swarm(store, relay, inbox=relay), relay), a.port, not a.no_browser)
