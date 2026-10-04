from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..llm import LLM
from ..store import Store


@dataclass
class Ctx:
    store: Store
    cfg: Config
    llm: LLM | None          # None = templates only
    now: Callable[[], float]
    rng: random.Random = field(default_factory=random.Random)
