from __future__ import annotations

from typing import Any

from .base import InferenceBackend


def create_backend(backend_name: str, model_name: str, model_cfg: dict[str, Any], logger: Any) -> InferenceBackend:
    b = backend_name.strip().lower()
    if b == "vllm":
        from .vllm_backend import VLLMBackend

        return VLLMBackend(model_name=model_name, model_cfg=model_cfg, logger=logger)
    if b == "transformers":
        from .hf_backend import HFTransformersBackend

        return HFTransformersBackend(model_name=model_name, model_cfg=model_cfg, logger=logger)
    raise ValueError(f"Unknown backend: {backend_name}")
