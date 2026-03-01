from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenerationParams:
    max_new_tokens: int = 512
    temperature: float = 0.1
    top_p: float = 0.9
    timeout_sec: int = 120


class InferenceBackend(ABC):
    def __init__(self, model_name: str, model_cfg: dict, logger):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.logger = logger

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str, params: GenerationParams) -> str:
        raise NotImplementedError

    def close(self) -> None:
        return None
