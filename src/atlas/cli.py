from __future__ import annotations

import argparse

from .agents.intake import Intake
from .channels.relay import RelayChannel
from .agents import Ctx
from .config import Config
from .llm import TemplateLLM
from .store import Store
from .swarm import Swarm
import time


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="atlas")
    p.add_argument("--db", default="atlas.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="load leads from a CSV with name,phone,company")
    imp.add_argument("csv")
    sub.add_parser("tick", help="run the swarm once")
    a = p.parse_args(argv)
    store = Store(a.db)
    if a.cmd == "import":
        r = Intake().run(Ctx(store, Config(), TemplateLLM(), time.time), a.csv)
        print(f"imported {r.done}, skipped {r.skipped}")
    else:
        for r in Swarm(store, RelayChannel()).tick():
            print(f"{r.agent}: done={r.done} skipped={r.skipped}")
