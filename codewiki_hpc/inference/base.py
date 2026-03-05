from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenerationParams:
    max_new_tokens: int = 512
    temperature: float = 0.1
    top_p: float = 0.9
    timeout_sec: int = 120


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    generation_calls: int = 0


class InferenceBackend(ABC):
    def __init__(self, model_name: str, model_cfg: dict, logger):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.logger = logger
        self._usage = TokenUsage()

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str, params: GenerationParams) -> str:
        raise NotImplementedError

    def _record_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        p = max(0, int(prompt_tokens))
        c = max(0, int(completion_tokens))
        self._usage.prompt_tokens += p
        self._usage.completion_tokens += c
        self._usage.total_tokens += (p + c)
        self._usage.generation_calls += 1

    def usage_snapshot(self) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self._usage.prompt_tokens,
            completion_tokens=self._usage.completion_tokens,
            total_tokens=self._usage.total_tokens,
            generation_calls=self._usage.generation_calls,
        )

    def usage_delta(self, previous: TokenUsage) -> TokenUsage:
        current = self._usage
        return TokenUsage(
            prompt_tokens=max(0, current.prompt_tokens - previous.prompt_tokens),
            completion_tokens=max(0, current.completion_tokens - previous.completion_tokens),
            total_tokens=max(0, current.total_tokens - previous.total_tokens),
            generation_calls=max(0, current.generation_calls - previous.generation_calls),
        )

    def close(self) -> None:
        return None
