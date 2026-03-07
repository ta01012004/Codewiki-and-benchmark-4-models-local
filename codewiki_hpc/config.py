from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("configs/default.yaml")


MODEL_ALIASES = {
    "codellama": "CodeLlama",
    "deepseekcoder": "DeepSeekCoder",
    "mistral": "Mistral",
    "qwen": "Qwen",
}


def _normalize_model_alias(name: str) -> str:
    key = name.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    return MODEL_ALIASES.get(key, name.strip())


def parse_model_list(model_arg: str | None, config: dict[str, Any]) -> list[str]:
    if not model_arg:
        return list(config.get("models", {}).keys())
    items = [x for x in (part.strip() for part in model_arg.split(",")) if x]
    parsed = [_normalize_model_alias(x) for x in items]
    missing = [m for m in parsed if m not in config.get("models", {})]
    if missing:
        known = ", ".join(config.get("models", {}).keys())
        raise ValueError(f"Unknown model aliases: {missing}. Known models: {known}")
    return parsed


def deep_update(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_update(dst[key], value)
        else:
            dst[key] = value
    return dst


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    try:
        import yaml
    except Exception as e:
        raise RuntimeError("PyYAML is required to load config files. Install dependencies from requirements.txt") from e

    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("Config root must be a mapping")
    return cfg


def apply_cli_overrides(config: dict[str, Any], args: Any) -> dict[str, Any]:
    cfg = copy.deepcopy(config)

    if getattr(args, "pipeline_version", None):
        cfg["pipeline_version"] = str(args.pipeline_version)
    if getattr(args, "backend", None):
        cfg["backend"] = args.backend
    if getattr(args, "split", None):
        cfg["dataset"]["split"] = args.split
    if getattr(args, "max_repos", None) is not None:
        cfg["dataset"]["max_repos"] = int(args.max_repos)
    if getattr(args, "output_dir", None):
        cfg["output_dir"] = args.output_dir
    if getattr(args, "gpus", None) is not None:
        cfg.setdefault("runtime", {})["gpus"] = int(args.gpus)
    if getattr(args, "resume", None):
        cfg.setdefault("runtime", {})["resume"] = True
    if getattr(args, "seed", None) is not None:
        cfg.setdefault("runtime", {})["seed"] = int(args.seed)
    if getattr(args, "deterministic", None):
        cfg.setdefault("runtime", {})["deterministic"] = True
    if getattr(args, "offline", None):
        cfg.setdefault("runtime", {})["offline"] = True
    if getattr(args, "model_root", None):
        cfg.setdefault("runtime", {})["model_root"] = str(args.model_root)

    return cfg


def pretty_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
