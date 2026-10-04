"""The orchestrator. Order is the safety property: listen, then write, then send."""
from __future__ import annotations

import time

from .agents import Ctx
from .agents.responder import Responder
from .agents.sender import Sender
from .agents.writer import Writer
from .channels import Channel
from .config import Config
from .llm import LLM, TemplateLLM
from .models import Report
from .store import Store


class Swarm:
    def __init__(self, store: Store, channel: Channel, cfg: Config | None = None,
                 llm: LLM | None = None, now=time.time):
        self.ctx = Ctx(store, cfg or Config(), llm or TemplateLLM(), now)
        self.agents = [Responder(), Writer(), Sender(channel)]

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
