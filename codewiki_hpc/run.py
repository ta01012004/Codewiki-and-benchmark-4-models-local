from __future__ import annotations

import argparse
import csv
import os
import random
import traceback
from pathlib import Path
from typing import Any

from .config import apply_cli_overrides, load_config, parse_model_list
from .evaluation import (
    aggregate_model_matrix,
    compute_quality_score,
    infer_primary_language,
    write_model_matrix_csv,
    write_repo_matrix_csv,
)
from .evaluation.qa_eval import QAEvaluator
from .evaluation.rubric_eval import evaluate_rubrics, evaluate_rubrics_matrix
from .inference import create_backend
from .repo_manager import RepoManager
from .summarizer import RepoSummarizer
from .summarizer_v2 import RepoSummarizerV2
from .utils.logging import setup_logging
from .utils.text import read_text_safe


RESULT_COLUMNS = [
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CodeWiki HPC generation + evaluation pipeline")
    parser.add_argument("--config", type=str, default="configs/default.yaml")
    parser.add_argument("--models", type=str, default=None, help="Comma-separated aliases")
    parser.add_argument("--repos", type=str, default=None, help="Comma-separated repo_name filters")
    parser.add_argument("--split", type=str, default=None)
    parser.add_argument("--max_repos", type=int, default=None)
    parser.add_argument("--backend", type=str, default=None, choices=["vllm", "transformers"])
    parser.add_argument("--gpus", type=int, default=None)
    parser.add_argument("--output_dir", type=str, default=None)
    parser.add_argument("--pipeline_version", type=str, default=None, choices=["v1", "v2"])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--model_root", type=str, default=None)
    parser.add_argument("--eval_only", action="store_true")
    parser.add_argument("--array_index", type=int, default=None)
    parser.add_argument("--array_total", type=int, default=None)
    return parser.parse_args()


def set_seed(seed: int, deterministic: bool = False) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        if deterministic:
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
    except Exception:
        return


def _sample_shard(samples: list[Any], array_idx: int | None, array_total: int | None) -> list[Any]:
    if array_idx is None or array_total is None:
        return samples
    if array_total <= 0:
        return samples
    return [s for s in samples if (s.idx % array_total) == array_idx]


def _parse_repo_filters(repo_arg: str | None) -> list[str]:
    if not repo_arg:
        return []
    return [item.strip() for item in repo_arg.split(",") if item.strip()]


def _filter_samples_by_repo(samples: list[Any], repo_filters: list[str], logger: Any) -> list[Any]:
    if not repo_filters:
        return samples
    wanted = {name.lower(): name for name in repo_filters}
    filtered = [sample for sample in samples if sample.repo_name.lower() in wanted]
    found = {sample.repo_name.lower() for sample in filtered}
    missing = [name for name in repo_filters if name.lower() not in found]
    logger.info(f"Repo filter requested={len(repo_filters)} matched={len(filtered)}")
    if missing:
        logger.warning(f"Repo filters not found in loaded split: {missing}")
    return filtered


def _load_existing_keys(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    keys = set()
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            notes = (row.get("notes") or "").lower()
            doc_path = row.get("doc_path") or ""
            # Resume should skip only successful rows, and retry failed/empty-doc rows.
            if "pipeline_failure" in notes or "backend_init_failed" in notes or not doc_path.strip():
                continue
            keys.add(f"{row.get('repo_name','')}@{row.get('commit_id','')}")
    return keys


def _write_results(csv_path: Path, rows: list[dict[str, Any]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in rows:
            payload = {k: row.get(k, "") for k in RESULT_COLUMNS}
            writer.writerow(payload)


def _row_key(row: dict[str, Any]) -> str:
    return f"{row.get('repo_name','')}@{row.get('commit_id','')}"


def _upsert_row(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    key = _row_key(row)
    for i, existing in enumerate(rows):
        if _row_key(existing) == key:
            rows[i] = row
            return
    rows.append(row)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Keep last occurrence for each repo@commit key.
    latest_by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        key = _row_key(row)
        if key not in latest_by_key:
            order.append(key)
        latest_by_key[key] = row
    return [latest_by_key[k] for k in order]


def _pipeline_suffix(pipeline_version: str) -> str:
    return "_V2" if pipeline_version == "v2" else ""


def _results_dir(output_dir: Path, pipeline_version: str) -> Path:
    return output_dir / ("results_v2" if pipeline_version == "v2" else "results")


def _cache_dir(output_dir: Path, pipeline_version: str) -> Path:
    return output_dir / ("cache_v2" if pipeline_version == "v2" else "cache")


def _model_csv_name(model_cfg: dict[str, Any], fallback_key: str, pipeline_version: str) -> str:
    name = model_cfg.get("output_name") or model_cfg.get("model_name", fallback_key).split("/")[-1]
    return f"cwbench_{name}{_pipeline_suffix(pipeline_version)}.csv"


def _default_doc_path(output_dir: Path, model_key: str, repo_name: str, pipeline_version: str) -> Path:
    docs_dir_name = "docs_v2" if pipeline_version == "v2" else "docs"
    return output_dir / docs_dir_name / model_key / f"{repo_name}.md"


def main() -> None:
    args = parse_args()
    cfg = apply_cli_overrides(load_config(args.config), args)

    output_dir = Path(cfg.get("output_dir", "outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline_version = str(cfg.get("pipeline_version", "v1")).lower().strip()
    if pipeline_version not in {"v1", "v2"}:
        raise ValueError(f"Unsupported pipeline_version={pipeline_version}. Expected 'v1' or 'v2'.")
    results_dir = _results_dir(output_dir, pipeline_version)
    context_cache_dir = _cache_dir(output_dir, pipeline_version)

    logger = setup_logging(output_dir, run_name="codewiki_hpc")
    logger.info(f"Pipeline version: {pipeline_version}")

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    runtime_cfg = cfg.get("runtime", {})
    seed = int(runtime_cfg.get("seed", 42))
    deterministic = bool(runtime_cfg.get("deterministic", False))
    offline = bool(
        runtime_cfg.get("offline", False)
        or os.getenv("HF_HUB_OFFLINE", "0") == "1"
        or os.getenv("TRANSFORMERS_OFFLINE", "0") == "1"
    )
    model_root = runtime_cfg.get("model_root") or os.getenv("CODEWIKI_MODEL_ROOT")
    set_seed(seed, deterministic)

    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
        logger.info(f"Offline mode enabled (model_root={model_root or 'auto-detect'})")

    array_idx = args.array_index
    if array_idx is None and os.getenv("SLURM_ARRAY_TASK_ID") is not None:
        array_idx = int(os.getenv("SLURM_ARRAY_TASK_ID"))

    array_total = args.array_total
    if array_total is None and os.getenv("SLURM_ARRAY_TASK_COUNT") is not None:
        array_total = int(os.getenv("SLURM_ARRAY_TASK_COUNT"))

    dataset_cfg = cfg.get("dataset", {})
    split = dataset_cfg.get("split", "train")
    max_repos = dataset_cfg.get("max_repos", None)
    from .dataset import load_codewikibench

    samples = load_codewikibench(split=split, max_repos=max_repos, verbose_warnings=True)
    repo_filters = _parse_repo_filters(args.repos)
    samples = _filter_samples_by_repo(samples, repo_filters, logger)
    samples = _sample_shard(samples, array_idx=array_idx, array_total=array_total)
    logger.info(f"Total samples after sharding: {len(samples)}")

    selected_model_keys = parse_model_list(args.models, cfg)
    logger.info(f"Selected models: {selected_model_keys}")

    repo_manager = RepoManager(cache_dir=output_dir / "cache", cfg=cfg, logger=logger)
    summarizer = RepoSummarizerV2(cfg=cfg, logger=logger) if pipeline_version == "v2" else RepoSummarizer(cfg=cfg, logger=logger)
    if offline:
        repo_cache_dir = output_dir / "cache" / "repos"
        cached_repo_count = len(list(repo_cache_dir.glob("*"))) if repo_cache_dir.exists() else 0
        logger.info(f"Offline repo cache status: {cached_repo_count} cached repos at {repo_cache_dir}")
        if cached_repo_count == 0:
            logger.warning("Offline mode with empty repo cache will produce pipeline failures and zero scores.")

    eval_cfg = cfg.get("evaluation", {})
    qa_model_name = eval_cfg.get("sentence_transformer_model", "sentence-transformers/all-MiniLM-L6-v2")
    qa_evaluator = QAEvaluator(embed_model_name=qa_model_name)

    all_rows: list[dict[str, Any]] = []

    backend_name = str(cfg.get("backend", "vllm"))
    clone_timeout = int(runtime_cfg.get("clone_timeout_sec", 600))

    for model_key in selected_model_keys:
        model_cfg = dict(cfg["models"][model_key])
        model_cfg["offline"] = offline
        if model_root:
            model_cfg["model_root"] = model_root
        model_name = model_cfg["model_name"]
        csv_name = _model_csv_name(model_cfg, model_key, pipeline_version)
        csv_path = results_dir / csv_name

        model_rows: list[dict[str, Any]] = []
        if args.resume and csv_path.exists():
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                model_rows = list(csv.DictReader(f))
            model_rows = _dedupe_rows(model_rows)
        done_keys = _load_existing_keys(csv_path) if args.resume else set()

        logger.info(f"Running model={model_key} ({model_name}) on {len(samples)} repos")

        backend = None
        if not args.eval_only:
            try:
                backend = create_backend(backend_name, model_name, model_cfg, logger)
            except Exception as e:
                logger.error(f"Backend init failed for model={model_key}: {e}")
                for sample in samples:
                    key = f"{sample.repo_name}@{sample.commit_id}"
                    if key in done_keys:
                        continue
                    row = {
                        "repo_name": sample.repo_name,
                        "commit_id": sample.commit_id,
                        "model_name": model_name,
                        "primary_language": infer_primary_language(sample.docs_tree, sample.structured_docs),
                        "doc_path": "",
                        "generation_time_sec": 0.0,
                        "qa_pairs_count": len(sample.qa_pairs or []),
                        "qa_available": 1 if len(sample.qa_pairs or []) > 0 else 0,
                        "qa_score": 0.0,
                        "quality_score": 0.0,
                        "coverage_score": 0.0,
                        "hierarchy_alignment": 0.0,
                        "leaf_coverage": 0.0,
                        "structural_fidelity": 0.0,
                        "factual_grounding": 0.0,
                        "actionability": 0.0,
                        "coherence": 0.0,
                        "required_sections_coverage": 0.0,
                        "key_term_coverage": 0.0,
                        "leaf_items_hit": 0,
                        "leaf_items_total": 0,
                        "notes": f"backend_init_failed: {e}",
                    }
                    _upsert_row(model_rows, row)
                _write_results(csv_path, model_rows)
                all_rows.extend(model_rows)
                continue

        for sample in samples:
            key = f"{sample.repo_name}@{sample.commit_id}"
            if args.resume and key in done_keys:
                logger.info(f"[resume-skip] {key}")
                continue

            row: dict[str, Any] = {
                "repo_name": sample.repo_name,
                "commit_id": sample.commit_id,
                "model_name": model_name,
                "primary_language": infer_primary_language(sample.docs_tree, sample.structured_docs),
                "doc_path": "",
                "generation_time_sec": 0.0,
                "qa_pairs_count": len(sample.qa_pairs or []),
                "qa_available": 1 if len(sample.qa_pairs or []) > 0 else 0,
                "qa_score": 0.0,
                "quality_score": 0.0,
                "coverage_score": 0.0,
                "hierarchy_alignment": 0.0,
                "leaf_coverage": 0.0,
                "structural_fidelity": 0.0,
                "factual_grounding": 0.0,
                "actionability": 0.0,
                "coherence": 0.0,
                "required_sections_coverage": 0.0,
                "key_term_coverage": 0.0,
                "leaf_items_hit": 0,
                "leaf_items_total": 0,
                "notes": "",
            }

            notes: list[str] = []

            try:
                if args.eval_only:
                    doc_path = _default_doc_path(output_dir, model_key, sample.repo_name, pipeline_version)
                    row["doc_path"] = str(doc_path)
                else:
                    repo_path = repo_manager.prepare_repo(sample, timeout_sec=clone_timeout)
                    ctx = repo_manager.build_context(sample, repo_path)

                    context_json = context_cache_dir / sample.repo_name / "context.json"
                    if not context_json.exists():
                        repo_manager.dump_context_json(ctx, context_json)

                    gen_meta = summarizer.summarize_repo(
                        ctx=ctx,
                        model_key=model_key,
                        backend=backend,
                        model_gen_cfg=model_cfg,
                        output_dir=output_dir,
                        resume=bool(cfg.get("runtime", {}).get("resume", False) or args.resume),
                    )

                    row["doc_path"] = gen_meta.get("doc_path", "")
                    row["generation_time_sec"] = gen_meta.get("generation_time_sec", 0.0)

                doc_text = read_text_safe(row["doc_path"], max_chars=None)
                if not doc_text:
                    notes.append("doc_missing_or_empty")

                qa_result = qa_evaluator.evaluate(doc_text, sample.qa_pairs)
                row["qa_score"] = round(float(qa_result.score), 6)
                if not sample.qa_pairs:
                    notes.append("qa_pairs_missing")

                coverage_score, coverage_notes = evaluate_rubrics(
                    doc_text=doc_text,
                    rubrics=sample.rubrics,
                    docs_tree=sample.docs_tree,
                    structured_docs=sample.structured_docs,
                )
                row["coverage_score"] = round(float(coverage_score), 6)
                notes.append(coverage_notes)

                matrix_result = evaluate_rubrics_matrix(
                    doc_text=doc_text,
                    rubrics=sample.rubrics,
                    docs_tree=sample.docs_tree,
                    structured_docs=sample.structured_docs,
                )
                row["hierarchy_alignment"] = round(float(matrix_result.hierarchy_alignment), 6)
                row["leaf_coverage"] = round(float(matrix_result.leaf_coverage), 6)
                row["structural_fidelity"] = round(float(matrix_result.structural_fidelity), 6)
                row["factual_grounding"] = round(float(matrix_result.factual_grounding), 6)
                row["actionability"] = round(float(matrix_result.actionability), 6)
                row["coherence"] = round(float(matrix_result.coherence), 6)
                row["required_sections_coverage"] = round(float(matrix_result.required_sections_coverage), 6)
                row["key_term_coverage"] = round(float(matrix_result.key_term_coverage), 6)
                row["leaf_items_hit"] = int(matrix_result.leaf_items_hit)
                row["leaf_items_total"] = int(matrix_result.leaf_items_total)

                row["quality_score"] = round(compute_quality_score(row), 6)
                notes.append(f"matrix: {matrix_result.notes}")

            except Exception as e:
                notes.append(f"pipeline_failure: {type(e).__name__}: {e}")
                notes.append(traceback.format_exc(limit=2).replace("\n", " | "))
                logger.error(
                    f"repo failed: repo={sample.repo_name} commit={sample.commit_id} model={model_key} error={type(e).__name__}: {e}"
                )

            row["notes"] = "; ".join(notes)
            _upsert_row(model_rows, row)

            _write_results(csv_path, model_rows)
            if "pipeline_failure" in row["notes"]:
                logger.warning(
                    f"repo processed with failure: repo={sample.repo_name} commit={sample.commit_id} model={model_key}"
                )
            else:
                logger.info(
                    "repo processed",
                    extra={
                        "repo": sample.repo_name,
                        "commit": sample.commit_id,
                        "model": model_key,
                        "qa_score": row["qa_score"],
                        "coverage_score": row["coverage_score"],
                    },
                )

        if backend is not None:
            backend.close()

        _write_results(csv_path, model_rows)
        all_rows.extend(model_rows)

    all_csv = results_dir / f"cwbench_all_models{_pipeline_suffix(pipeline_version)}.csv"
    _write_results(all_csv, all_rows)
    logger.info(f"Wrote aggregate results to {all_csv}")

    repo_matrix_csv = results_dir / f"cwbench_matrix_repo{_pipeline_suffix(pipeline_version)}.csv"
    write_repo_matrix_csv(repo_matrix_csv, all_rows)
    logger.info(f"Wrote repo-level evaluation matrix to {repo_matrix_csv}")

    model_matrix_rows = aggregate_model_matrix(all_rows)
    model_matrix_csv = results_dir / f"cwbench_matrix_model{_pipeline_suffix(pipeline_version)}.csv"
    write_model_matrix_csv(model_matrix_csv, model_matrix_rows)
    logger.info(f"Wrote model-level evaluation matrix to {model_matrix_csv}")


if __name__ == "__main__":
    main()
