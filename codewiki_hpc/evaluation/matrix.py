from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .metrics import mean


MATRIX_REPO_COLUMNS = [
    "repo_name",
    "commit_id",
    "model_name",
    "primary_language",
    "doc_path",
    "generation_time_sec",
    "qa_pairs_count",
    "qa_available",
    "qa_score",
    "quality_score",
    "coverage_score",
    "hierarchy_alignment",
    "leaf_coverage",
    "structural_fidelity",
    "factual_grounding",
    "actionability",
    "coherence",
    "required_sections_coverage",
    "key_term_coverage",
    "leaf_items_hit",
    "leaf_items_total",
    "notes",
]


MATRIX_MODEL_COLUMNS = [
    "model_name",
    "repos_total",
    "repos_completed",
    "completion_rate",
    "mean_generation_time_sec",
    "qa_available_rate",
    "mean_qa_score",
    "mean_quality_score",
    "mean_coverage_score",
    "mean_hierarchy_alignment",
    "mean_leaf_coverage",
    "mean_structural_fidelity",
    "mean_factual_grounding",
    "mean_actionability",
    "mean_coherence",
    "mean_required_sections_coverage",
    "mean_key_term_coverage",
]


def _flatten_text_chunks(x: Any) -> list[str]:
    chunks: list[str] = []
    if x is None:
        return chunks
    if isinstance(x, str):
        chunks.append(x)
    elif isinstance(x, dict):
        for k, v in x.items():
            chunks.append(str(k))
            chunks.extend(_flatten_text_chunks(v))
    elif isinstance(x, list):
        for item in x:
            chunks.extend(_flatten_text_chunks(item))
    else:
        chunks.append(str(x))
    return chunks


def infer_primary_language(docs_tree: Any, structured_docs: Any) -> str:
    text = " ".join(_flatten_text_chunks(docs_tree) + _flatten_text_chunks(structured_docs)).lower()
    hints = [
        ("python", [".py", "requirements.txt", "pyproject.toml", "setup.py"]),
        ("typescript", [".ts", ".tsx", "tsconfig.json"]),
        ("javascript", [".js", ".jsx", "package.json", "node_modules"]),
        ("java", [".java", "pom.xml", "build.gradle", "gradle"]),
        ("csharp", [".cs", ".csproj", ".sln", "dotnet"]),
        ("cpp", [".cpp", ".hpp", "cmakelists.txt", "c++", ".cc", ".h"]),
        ("go", [".go", "go.mod"]),
        ("rust", [".rs", "cargo.toml"]),
        ("php", [".php", "composer.json"]),
        ("ruby", [".rb", "gemfile"]),
    ]
    best_lang = "unknown"
    best_score = 0
    for lang, tokens in hints:
        score = sum(1 for t in tokens if t in text)
        if score > best_score:
            best_score = score
            best_lang = lang
    return best_lang


def write_repo_matrix_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MATRIX_REPO_COLUMNS)
        writer.writeheader()
        for row in rows:
            payload = {k: row.get(k, "") for k in MATRIX_REPO_COLUMNS}
            writer.writerow(payload)


def compute_quality_score(row: dict[str, Any]) -> float:
    # Base weights aligned with rubric-first evaluation and optional QA-as-judge.
    weights = {
        "coverage_score": 0.25,
        "hierarchy_alignment": 0.20,
        "leaf_coverage": 0.15,
        "structural_fidelity": 0.15,
        "factual_grounding": 0.10,
        "actionability": 0.05,
        "coherence": 0.05,
        "qa_score": 0.05,
    }

    qa_pairs_count = int(row.get("qa_pairs_count", 0) or 0)
    if qa_pairs_count <= 0:
        # Renormalize without QA when benchmark sample has no QA pairs.
        weights["qa_score"] = 0.0

    total_w = sum(weights.values())
    if total_w <= 0:
        return 0.0

    s = 0.0
    for key, w in weights.items():
        if w <= 0:
            continue
        try:
            v = float(row.get(key, 0.0))
        except Exception:
            v = 0.0
        s += w * max(0.0, min(1.0, v))
    return max(0.0, min(1.0, s / total_w))


def aggregate_model_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        model = str(row.get("model_name", "unknown"))
        by_model.setdefault(model, []).append(row)

    output: list[dict[str, Any]] = []
    for model, items in by_model.items():
        total = len(items)
        completed = sum(1 for r in items if str(r.get("doc_path", "")).strip())
        qa_available = sum(1 for r in items if int(r.get("qa_pairs_count", 0) or 0) > 0)

        def _m(key: str) -> float:
            vals = []
            for r in items:
                try:
                    vals.append(float(r.get(key, 0.0)))
                except Exception:
                    continue
            return mean(vals)

        output.append(
            {
                "model_name": model,
                "repos_total": total,
                "repos_completed": completed,
                "completion_rate": round((completed / total), 6) if total > 0 else 0.0,
                "mean_generation_time_sec": round(_m("generation_time_sec"), 6),
                "qa_available_rate": round((qa_available / total), 6) if total > 0 else 0.0,
                "mean_qa_score": round(_m("qa_score"), 6),
                "mean_quality_score": round(_m("quality_score"), 6),
                "mean_coverage_score": round(_m("coverage_score"), 6),
                "mean_hierarchy_alignment": round(_m("hierarchy_alignment"), 6),
                "mean_leaf_coverage": round(_m("leaf_coverage"), 6),
                "mean_structural_fidelity": round(_m("structural_fidelity"), 6),
                "mean_factual_grounding": round(_m("factual_grounding"), 6),
                "mean_actionability": round(_m("actionability"), 6),
                "mean_coherence": round(_m("coherence"), 6),
                "mean_required_sections_coverage": round(_m("required_sections_coverage"), 6),
                "mean_key_term_coverage": round(_m("key_term_coverage"), 6),
            }
        )

    return sorted(output, key=lambda x: x["model_name"])


def write_model_matrix_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MATRIX_MODEL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in MATRIX_MODEL_COLUMNS})


def matrix_row_to_markdown(row: dict[str, Any]) -> str:
    keys = [
        "quality_score",
        "coverage_score",
        "hierarchy_alignment",
        "leaf_coverage",
        "structural_fidelity",
        "factual_grounding",
        "actionability",
        "coherence",
    ]
    parts = []
    for k in keys:
        try:
            v = float(row.get(k, 0.0))
        except Exception:
            v = 0.0
        parts.append(f"{k}={v:.3f}")
    return ", ".join(parts)
