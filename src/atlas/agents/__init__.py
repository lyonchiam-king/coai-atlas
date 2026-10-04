from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import Config
from ..llm import LLM
from ..store import Store


@dataclass
class Ctx:
    store: Store
    cfg: Config
    llm: LLM
    now: Callable[[], float]
