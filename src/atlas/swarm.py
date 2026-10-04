"""The orchestrator. Order is the safety property: listen, then write, then send."""
from __future__ import annotations

import random
import time

from .agents import Ctx
from .agents.checker import Checker
from .agents.listener import Listener
from .agents.responder import Responder
from .agents.sender import Sender
from .agents.writer import Writer
from .channels import Channel
from .config import Config
from .llm import LLM
from .models import Report
from .pacing import Pacer
from .store import Store


class Swarm:
    """One shared brain, one set of hands per phone.

    `channels` maps a phone's account id to its relay. The single-`channel`
    form is the one-phone case and is what most tests use.
    """

    def __init__(self, store: Store, channel: Channel | None = None, cfg: Config | None = None,
                 llm: LLM | None = None, now=time.time, inbox=None, rng: random.Random | None = None,
                 channels: dict[str, Channel] | None = None):
        # llm=None means templates. The CLI decides whether a real writer exists.
        self.ctx = Ctx(store, cfg or Config(), llm, now, rng or random.Random())
        if channels is None:
            channels = {"1": channel} if channel is not None else {}
            inboxes = {"1": inbox} if inbox is not None else {}
        else:
            inboxes = {a: ch for a, ch in channels.items() if hasattr(ch, "poll")}
        self.channels = channels
        self.pacers = {a: Pacer(store, self.ctx.cfg, self.ctx.rng, a) for a in channels}
        # Only phones whose relay can look numbers up require a "yes" before writing.
        checkable = {a: ch for a, ch in channels.items() if hasattr(ch, "check")}
        self.ctx.checked_accounts = set(checkable)
        # Listen, classify, check, write, send. A STOP that arrived since the last
        # tick is stored and acted on before anything is sent.
        self.agents = [
            *[Listener(src, a) for a, src in inboxes.items()],
            Responder(),
            *[Checker(ch, a) for a, ch in checkable.items()],
            Writer(),
            *[Sender(ch, self.pacers[a], a) for a, ch in channels.items()],
        ]

    @property
    def pacer(self) -> Pacer:
        return next(iter(self.pacers.values()))

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
