from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import os
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from .base import GenerationParams, InferenceBackend


class HFTransformersBackend(InferenceBackend):
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
        offline = bool(
            model_cfg.get("offline", False)
            or os.getenv("HF_HUB_OFFLINE", "0") == "1"
            or os.getenv("TRANSFORMERS_OFFLINE", "0") == "1"
        )
        model_source = self._resolve_model_source(model_name, model_cfg)
        local_files_only = bool(offline)
        cache_dir = model_cfg.get("cache_dir") or os.getenv("TRANSFORMERS_CACHE")

        if offline and model_source == model_name:
            user = os.getenv("USER", "<user>")
            expected = model_cfg.get("model_root") or os.getenv("CODEWIKI_MODEL_ROOT") or f"/work/{user}/models"
            short = model_name.split("/")[-1]
            raise RuntimeError(
                "Offline mode is enabled but no local model directory was found. "
                f"Expected something like: {expected}/{short} "
                "(or set models.<alias>.local_path / --model_root / CODEWIKI_MODEL_ROOT)."
            )

        torch_dtype = model_cfg.get("torch_dtype", "auto")
        if torch_dtype == "auto":
            dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        else:
            dtype = getattr(torch, torch_dtype)

        device_map = model_cfg.get("device_map", "auto")
        self.max_input_tokens = int(model_cfg.get("max_input_tokens", 4096))

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            trust_remote_code=True,
            local_files_only=local_files_only,
            cache_dir=cache_dir,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_source,
            trust_remote_code=True,
            torch_dtype=dtype,
            device_map=device_map,
            local_files_only=local_files_only,
            cache_dir=cache_dir,
        )

        if self.tokenizer.pad_token is None and self.tokenizer.eos_token is not None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.logger.info(
            f"Initialized HF Transformers backend for {model_name} "
            f"(source={model_source}, offline={offline})"
        )

    def _build_inputs(
        self,
        system_prompt: str,
        user_prompt: str,
        max_input_tokens: int | None = None,
    ) -> dict[str, torch.Tensor]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception:
            prompt = f"System: {system_prompt}\n\nUser: {user_prompt}\n\nAssistant:"

        max_len = int(max_input_tokens or self.max_input_tokens)
        return self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=max_len,
        )

    def _generate_once(
        self,
        inputs: dict[str, torch.Tensor],
        params: GenerationParams,
        max_tokens: int,
    ) -> tuple[str, int]:
        model_device = getattr(self.model, "device", None)
        if model_device is not None and model_device.type != "meta":
            inputs = {k: v.to(model_device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=params.temperature > 0,
                temperature=params.temperature,
                top_p=params.top_p,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        gen_tokens = outputs[0][inputs["input_ids"].shape[1] :]
        completion_tokens = int(gen_tokens.shape[-1])
        text = self.tokenizer.decode(gen_tokens, skip_special_tokens=True).strip()
        return text, completion_tokens

    def generate(self, system_prompt: str, user_prompt: str, params: GenerationParams) -> str:
        max_tokens = int(params.max_new_tokens)
        input_limit = int(self.max_input_tokens)

        for attempt in range(4):
            try:
                inputs = self._build_inputs(system_prompt, user_prompt, max_input_tokens=input_limit)
                seq_len = int(inputs["input_ids"].shape[-1])
                self.logger.info(
                    f"transformers generate attempt={attempt + 1} input_tokens={seq_len} "
                    f"max_input_tokens={input_limit} max_new_tokens={max_tokens}"
                )
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(self._generate_once, inputs, params, max_tokens)
                    text, completion_tokens = fut.result(timeout=params.timeout_sec)
                self._record_usage(prompt_tokens=seq_len, completion_tokens=completion_tokens)
                return text
            except FuturesTimeout as e:
                raise TimeoutError(
                    f"Generation timed out for model={self.model_name} timeout={params.timeout_sec}s"
                ) from e
            except RuntimeError as e:
                msg = str(e).lower()
                if "out of memory" in msg or "cuda" in msg:
                    next_tokens = max(64, max_tokens // 2)
                    next_input_limit = max(1024, int(input_limit * 0.8))
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    self.logger.warning(
                        f"OOM in transformers generation; reducing max_new_tokens {max_tokens}->{next_tokens}, "
                        f"max_input_tokens {input_limit}->{next_input_limit} (attempt={attempt + 1})"
                    )
                    max_tokens = next_tokens
                    input_limit = next_input_limit
                    continue
                raise

        raise RuntimeError("Failed generation after OOM retries")

    def close(self) -> None:
        self.model = None
