from __future__ import annotations

from pathlib import Path
from typing import Any


def _score_file(path: str, size: int, fan_in: int, docs_paths: set[str]) -> float:
    p = path.lower()
    name = Path(p).name
    score = 0.0

    if p in docs_paths:
        score += 8.0

    if name in {
        "main.py",
        "train.py",
        "run.py",
        "app.py",
        "server.py",
        "__main__.py",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "pom.xml",
        "build.gradle",
        "cmakelists.txt",
    }:
        score += 10.0

    if any(token in p for token in ["core", "model", "engine", "service", "pipeline", "trainer"]):
        score += 4.0

    if p.startswith("src/"):
        score += 3.0
    if p.startswith("docs/"):
        score += 5.0

    # Prefer files with higher incoming dependency signal.
    score += min(float(fan_in), 10.0)

    # Slight penalty for overly large files to keep prompt budgets reasonable.
    if size > 200_000:
        score -= 6.0
    elif size > 100_000:
        score -= 3.0

    return score


def select_top_k_files(
    files: list[dict[str, Any]],
    docs_files: list[dict[str, Any]],
    module_map: dict[str, Any],
    top_k: int,
) -> list[dict[str, Any]]:
    fan_in: dict[str, int] = module_map.get("fan_in", {})
    docs_paths = {x["path"] for x in docs_files}

    scored: list[tuple[float, dict[str, Any]]] = []
    for item in files:
        score = _score_file(item["path"], int(item["size"]), int(fan_in.get(item["path"], 0)), docs_paths)
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [item for _, item in scored[:top_k]]

    # Always include at least one README if present.
    readmes = [f for f in files if Path(f["path"]).name.lower().startswith("readme")]
    if readmes and all(r["path"] not in {s["path"] for s in selected} for r in readmes):
        selected = selected[:-1] + [readmes[0]] if selected else [readmes[0]]

    return selected


def group_file_summaries_by_module(file_summaries: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for path, summary in file_summaries.items():
        parts = path.split("/")
        module = parts[0] if len(parts) > 1 else "root"
        grouped.setdefault(module, []).append((path, summary))
    return grouped
