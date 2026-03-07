from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


PAPER_BASELINES = [
    {
        "paper_system": "DeepWiki",
        "paper_quality_score_percent": 64.06,
        "paper_quality_std_percent": 3.60,
        "source": "CodeWiki paper (arXiv:2510.24428, v5, Table/Aggregate)",
        "source_url": "https://arxiv.org/abs/2510.24428",
    },
    {
        "paper_system": "CodeWiki-kimi-k2 (open-source)",
        "paper_quality_score_percent": 64.80,
        "paper_quality_std_percent": 4.07,
        "source": "CodeWiki paper (arXiv:2510.24428, v5, Table/Aggregate)",
        "source_url": "https://arxiv.org/abs/2510.24428",
    },
    {
        "paper_system": "CodeWiki-sonnet-4",
        "paper_quality_score_percent": 68.79,
        "paper_quality_std_percent": 3.84,
        "source": "CodeWiki paper (arXiv:2510.24428, v5, Table/Aggregate)",
        "source_url": "https://arxiv.org/abs/2510.24428",
    },
]


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def build_vs_paper_rows(model_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in model_rows:
        model_name = str(row.get("model_name", "unknown"))
        local_quality = float(row.get("mean_quality_score", 0.0) or 0.0) * 100.0
        local_coverage = float(row.get("mean_coverage_score", 0.0) or 0.0) * 100.0
        completion_rate = float(row.get("completion_rate", 0.0) or 0.0) * 100.0
        for base in PAPER_BASELINES:
            paper_q = float(base["paper_quality_score_percent"])
            out.append(
                {
                    "model_name": model_name,
                    "local_quality_score_percent": round(local_quality, 2),
                    "local_coverage_score_percent": round(local_coverage, 2),
                    "local_completion_rate_percent": round(completion_rate, 2),
                    "paper_system": base["paper_system"],
                    "paper_quality_score_percent": round(paper_q, 2),
                    "paper_quality_std_percent": round(float(base["paper_quality_std_percent"]), 2),
                    "delta_quality_percent_vs_paper": "",
                    "comparable_scale": "no",
                    "comparison_mode": "reference_only_non_comparable",
                    "comparison_note": (
                        "Local quality_score uses custom rubric heuristic scale; "
                        "paper quality uses LLM-as-a-Judge protocol. Use internal ranking only."
                    ),
                    "source": base["source"],
                    "source_url": base["source_url"],
                }
            )
    return out


def build_internal_ranking_rows(model_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        model_rows,
        key=lambda r: float(r.get("mean_quality_score", 0.0) or 0.0),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(ranked, start=1):
        out.append(
            {
                "rank": idx,
                "model_name": row.get("model_name", "unknown"),
                "mean_quality_score": round(float(row.get("mean_quality_score", 0.0) or 0.0), 6),
                "mean_coverage_score": round(float(row.get("mean_coverage_score", 0.0) or 0.0), 6),
                "mean_hierarchy_alignment": round(float(row.get("mean_hierarchy_alignment", 0.0) or 0.0), 6),
                "mean_leaf_coverage": round(float(row.get("mean_leaf_coverage", 0.0) or 0.0), 6),
                "mean_structural_fidelity": round(float(row.get("mean_structural_fidelity", 0.0) or 0.0), 6),
                "mean_factual_grounding": round(float(row.get("mean_factual_grounding", 0.0) or 0.0), 6),
                "mean_actionability": round(float(row.get("mean_actionability", 0.0) or 0.0), 6),
                "mean_coherence": round(float(row.get("mean_coherence", 0.0) or 0.0), 6),
                "qa_available_rate": round(float(row.get("qa_available_rate", 0.0) or 0.0), 6),
                "method_note": (
                    "Comparable within this run only (same evaluator, same data, same config)."
                ),
            }
        )
    return out


def generate_paper_comparison(results_dir: Path) -> Path:
    matrix_model = results_dir / "cwbench_matrix_model.csv"
    if not matrix_model.exists():
        raise FileNotFoundError(f"Missing input: {matrix_model}")

    model_rows = _read_rows(matrix_model)
    out_rows = build_vs_paper_rows(model_rows)

    out_csv = results_dir / "cwbench_vs_codewiki_paper.csv"
    fieldnames = [
        "model_name",
        "local_quality_score_percent",
        "local_coverage_score_percent",
        "local_completion_rate_percent",
        "paper_system",
        "paper_quality_score_percent",
        "paper_quality_std_percent",
        "delta_quality_percent_vs_paper",
        "comparable_scale",
        "comparison_mode",
        "comparison_note",
        "source",
        "source_url",
    ]
    _write_rows(out_csv, fieldnames, out_rows)

    ranking_rows = build_internal_ranking_rows(model_rows)
    ranking_csv = results_dir / "cwbench_internal_ranking.csv"
    ranking_fields = [
        "rank",
        "model_name",
        "mean_quality_score",
        "mean_coverage_score",
        "mean_hierarchy_alignment",
        "mean_leaf_coverage",
        "mean_structural_fidelity",
        "mean_factual_grounding",
        "mean_actionability",
        "mean_coherence",
        "qa_available_rate",
        "method_note",
    ]
    _write_rows(ranking_csv, ranking_fields, ranking_rows)
    return out_csv
