#!/usr/bin/env python3
import csv
import itertools
from collections import defaultdict
from pathlib import Path


def rank_order(score_map: dict[str, float]) -> list[str]:
    return [k for k, _ in sorted(score_map.items(), key=lambda x: (-x[1], x[0]))]


def spearman(order_a: list[str], order_b: list[str]) -> float:
    n = len(order_a)
    if n < 2:
        return 1.0
    rank_a = {k: i + 1 for i, k in enumerate(order_a)}
    rank_b = {k: i + 1 for i, k in enumerate(order_b)}
    d2 = sum((rank_a[k] - rank_b[k]) ** 2 for k in rank_a)
    return 1 - (6 * d2) / (n * (n * n - 1))


def main() -> None:
    base = Path("/home/22011107/TA/NLPCODEWIKI/CodeWiki/outputs/results_v2_improved_7repo")
    judge_csv = base / "judge_qwen_repo_v2_7repo.csv"
    default_summary = base / "cwbench_paper_style_judge_summary.csv"
    summary_out = base / "judge_qwen_summary_v2_7repo.csv"
    stability_out = base / "repo_ranking_stability_v2_7repo.csv"

    rows = list(csv.DictReader(judge_csv.open(encoding="utf-8")))
    rows = [r for r in rows if not (r.get("notes") or "").strip()]

    if default_summary.exists():
        summary_rows = list(csv.DictReader(default_summary.open(encoding="utf-8")))
        with summary_out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "model_name",
                    "repos_target",
                    "repos_scored",
                    "mean_weighted_quality_percent",
                    "mean_coverage_percent",
                ],
            )
            writer.writeheader()
            writer.writerows(summary_rows)

    by_model: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        by_model[row["model_key"]][row["repo_name"]] = float(row["weighted_quality_percent"] or 0.0)

    fields = [
        "section",
        "model_key",
        "model_a",
        "model_b",
        "rank",
        "repo_name",
        "quality_percent",
        "same_order",
        "spearman",
        "consensus_quality_percent",
    ]

    output_rows: list[dict[str, str | int | float]] = []

    for model_key in sorted(by_model):
        ordered = rank_order(by_model[model_key])
        for idx, repo_name in enumerate(ordered, 1):
            output_rows.append(
                {
                    "section": "model_ranking",
                    "model_key": model_key,
                    "rank": idx,
                    "repo_name": repo_name,
                    "quality_percent": round(by_model[model_key][repo_name], 2),
                }
            )

    models = sorted(by_model)
    for model_a, model_b in itertools.combinations(models, 2):
        order_a = rank_order(by_model[model_a])
        order_b = rank_order(by_model[model_b])
        output_rows.append(
            {
                "section": "pairwise_stability",
                "model_a": model_a,
                "model_b": model_b,
                "same_order": str(order_a == order_b).lower(),
                "spearman": round(spearman(order_a, order_b), 3),
            }
        )

    repo_scores: dict[str, list[float]] = defaultdict(list)
    for model_scores in by_model.values():
        for repo_name, score in model_scores.items():
            repo_scores[repo_name].append(score)
    consensus = {repo_name: sum(vals) / len(vals) for repo_name, vals in repo_scores.items()}
    for idx, (repo_name, score) in enumerate(sorted(consensus.items(), key=lambda x: (-x[1], x[0])), 1):
        output_rows.append(
            {
                "section": "consensus_ranking",
                "rank": idx,
                "repo_name": repo_name,
                "consensus_quality_percent": round(score, 2),
            }
        )

    with stability_out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in output_rows:
            writer.writerow({k: row.get(k, "") for k in fields})

    print(f"Wrote: {summary_out}")
    print(f"Wrote: {stability_out}")


if __name__ == "__main__":
    main()
