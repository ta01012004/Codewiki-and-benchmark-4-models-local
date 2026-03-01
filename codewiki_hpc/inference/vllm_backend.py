from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import os
from pathlib import Path
from typing import Any

from .base import GenerationParams, InferenceBackend


class VLLMBackend(InferenceBackend):
    def _resolve_model_source(self, model_name: str, model_cfg: dict[str, Any]) -> str:
        explicit = model_cfg.get("local_path")
        if explicit:
            p = Path(explicit).expanduser()
            if p.exists():
                return str(p)

        model_root = model_cfg.get("model_root") or os.getenv("CODEWIKI_MODEL_ROOT")
        if not model_root:
            user = os.getenv("USER")
            if user:
                model_root = f"/work/{user}/models"

        local_dir_name = model_cfg.get("local_dir_name") or model_name.split("/")[-1]
        if model_root:
            p = Path(model_root).expanduser() / local_dir_name
            if p.exists():
                return str(p)

        return model_name

    def __init__(self, model_name: str, model_cfg: dict[str, Any], logger: Any):
        super().__init__(model_name, model_cfg, logger)
        try:
            from vllm import LLM
        except ImportError as e:
            raise RuntimeError("vLLM backend requested but vllm is not installed") from e
        offline = bool(
            model_cfg.get("offline", False)
            or os.getenv("HF_HUB_OFFLINE", "0") == "1"
            or os.getenv("TRANSFORMERS_OFFLINE", "0") == "1"
        )
        model_source = self._resolve_model_source(model_name, model_cfg)
        if offline and model_source == model_name:
            user = os.getenv("USER", "<user>")
            expected = model_cfg.get("model_root") or os.getenv("CODEWIKI_MODEL_ROOT") or f"/work/{user}/models"
            short = model_name.split("/")[-1]
            raise RuntimeError(
                "Offline mode is enabled but no local model directory was found. "
                f"Expected something like: {expected}/{short} "
                "(or set models.<alias>.local_path / --model_root / CODEWIKI_MODEL_ROOT)."
            )

        tensor_parallel_size = int(model_cfg.get("tensor_parallel_size", 1))
        gpu_memory_utilization = float(model_cfg.get("gpu_memory_utilization", 0.92))
        max_model_len = int(model_cfg.get("max_model_len", 8192))
        dtype = model_cfg.get("dtype", "auto")

        self.llm = LLM(
            model=model_source,
            tensor_parallel_size=tensor_parallel_size,
            gpu_memory_utilization=gpu_memory_utilization,
            max_model_len=max_model_len,
            dtype=dtype,
            trust_remote_code=True,
        )

        self.tokenizer = self.llm.get_tokenizer()
        self.logger.info(
            f"Initialized vLLM backend for {model_name} "
            f"(source={model_source}, offline={offline})"
        )

    def _build_prompt(self, system_prompt: str, user_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception:
            return f"System: {system_prompt}\n\nUser: {user_prompt}\n\nAssistant:"

    def _generate_once(self, prompt: str, params: GenerationParams, max_tokens: int) -> str:
        from vllm import SamplingParams

        sampling_params = SamplingParams(
            max_tokens=max_tokens,
            temperature=params.temperature,
            top_p=params.top_p,
        )
        outputs = self.llm.generate([prompt], sampling_params=sampling_params, use_tqdm=False)
        if not outputs or not outputs[0].outputs:
            return ""
        return outputs[0].outputs[0].text.strip()

    def generate(self, system_prompt: str, user_prompt: str, params: GenerationParams) -> str:
        prompt = self._build_prompt(system_prompt, user_prompt)
        max_tokens = int(params.max_new_tokens)

        for attempt in range(4):
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(self._generate_once, prompt, params, max_tokens)
                    return fut.result(timeout=params.timeout_sec)
            except FuturesTimeout as e:
                raise TimeoutError(
                    f"Generation timed out for model={self.model_name} timeout={params.timeout_sec}s"
                ) from e
            except RuntimeError as e:
                msg = str(e).lower()
                if "out of memory" in msg or "cuda" in msg:
                    next_tokens = max(64, max_tokens // 2)
                    if next_tokens == max_tokens:
                        raise
                    self.logger.warning(
                        "OOM in vLLM generation; reducing max tokens",
                        extra={"from": max_tokens, "to": next_tokens, "attempt": attempt + 1},
                    )
                    max_tokens = next_tokens
                    continue
                raise

        raise RuntimeError("Failed generation after OOM retries")

    def close(self) -> None:
        self.llm = None
