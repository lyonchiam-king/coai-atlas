"""The orchestrator. Order is the safety property: listen, then write, then send."""
from __future__ import annotations

import random
import time

from .agents import Ctx
from .agents.listener import Listener
from .agents.responder import Responder
from .agents.sender import Sender
from .agents.writer import Writer
from .channels import Channel
from .config import Config
from .llm import LLM
from .pacing import Pacer
from .models import Report
from .store import Store


class Swarm:
    def __init__(self, store: Store, channel: Channel, cfg: Config | None = None,
                 llm: LLM | None = None, now=time.time, inbox=None, rng: random.Random | None = None):
        # llm=None means templates. The CLI decides whether a real writer exists.
        self.ctx = Ctx(store, cfg or Config(), llm, now, rng or random.Random())
        self.pacer = Pacer(store, self.ctx.cfg, self.ctx.rng)
        # Listen, classify, write, send. A STOP that arrived since the last tick must
        # be stored and acted on before anything is sent.
        listen = [Listener(inbox)] if inbox is not None else []
        self.agents = [*listen, Responder(), Writer(), Sender(channel, self.pacer)]

    def tick(self) -> list[Report]:
        reports = []
        for agent in self.agents:
            try:
                reports.append(agent.run(self.ctx))
            except Exception as e:   # a dead agent must not take the others down
                r = Report(agent.name)
                r.skip(f"crashed:{type(e).__name__}")
                reports.append(r)
        return reports
