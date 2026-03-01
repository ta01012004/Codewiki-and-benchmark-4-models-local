from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datasets import Dataset, load_dataset


@dataclass
class RepoSample:
    idx: int
    repo_name: str
    repo_url: str
    commit_id: str
    docs_tree: Any
    structured_docs: Any
    rubrics: Any
    qa_pairs: list[dict[str, str]]
    raw: dict[str, Any]


def _flatten_keys(record: dict[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for k, v in record.items():
        path = f"{prefix}.{k}" if prefix else k
        keys.append(path)
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, path))
    return keys


def _flatten_items(record: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for k, v in record.items():
        path = f"{prefix}.{k}" if prefix else k
        items.append((path, v))
        if isinstance(v, dict):
            items.extend(_flatten_items(v, path))
    return items


def _deep_get(record: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = record
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _pick_first(record: dict[str, Any], candidates: list[str]) -> Any:
    # Direct top-level and dotted-path lookups first.
    for key in candidates:
        if "." in key:
            val = _deep_get(record, key)
            if val not in (None, ""):
                return val
        elif key in record and record[key] not in (None, ""):
            return record[key]

    # Fallback: dynamic nested key detection by suffix/normalized matching.
    flat_items = _flatten_items(record)
    normalized_candidates = [c.lower().replace("_", "") for c in candidates]
    for path, val in flat_items:
        leaf = path.split(".")[-1]
        leaf_n = leaf.lower().replace("_", "")
        path_n = path.lower().replace("_", "")
        for c, c_n in zip(candidates, normalized_candidates):
            if val in (None, ""):
                continue
            if leaf == c or path.endswith(f".{c}") or leaf_n == c_n or path_n.endswith(c_n):
                return val
    return None


def _to_str_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_qa(raw_qa: Any) -> list[dict[str, str]]:
    if raw_qa is None:
        return []
    items: list[dict[str, str]] = []

    if isinstance(raw_qa, dict):
        # Handle packed schema like {"questions": [...], "answers": [...]}.
        q_list = _pick_first(raw_qa, ["questions", "question", "query"])
        a_list = _pick_first(raw_qa, ["answers", "answer", "references"])
        if isinstance(q_list, list) and isinstance(a_list, list) and len(q_list) == len(a_list):
            return [
                {"question": str(q), "answer": str(a)}
                for q, a in zip(q_list, a_list)
                if str(q).strip() and str(a).strip()
            ]
        raw_qa = [raw_qa]

    if isinstance(raw_qa, list):
        for item in raw_qa:
            if not isinstance(item, dict):
                continue
            q = _pick_first(item, ["question", "query", "prompt", "q"])
            a = _pick_first(item, ["answer", "reference_answer", "gold_answer", "a"])
            if q and a:
                items.append({"question": str(q), "answer": str(a)})

    return items


def normalize_record(idx: int, record: dict[str, Any], verbose_warnings: bool = False) -> RepoSample:
    repo_name = _pick_first(
        record,
        ["repo_name", "repository_name", "name", "repo", "project_name"],
    )
    repo_url = _pick_first(
        record,
        ["repo_url", "url", "repository_url", "git_url", "github_url"],
    )
    commit_id = _pick_first(record, ["commit_id", "commit", "sha", "revision", "hash"])

    docs_tree = _pick_first(record, ["docs_tree", "doc_tree", "documentation_tree"])
    structured_docs = _pick_first(record, ["structured_docs", "docs_structured", "document_structure"])
    rubrics = _pick_first(record, ["rubrics", "rubric", "evaluation_rubrics"])
    qa_raw = _pick_first(record, ["qa_pairs", "qa", "questions", "question_answer_pairs"])

    if not repo_name and repo_url:
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

    if verbose_warnings and (not repo_url or not commit_id):
        available = ", ".join(sorted(_flatten_keys(record)))
        print(
            "[dataset-warning] Missing repo_url/commit_id in sample "
            f"{idx}. Available keys: {available}"
        )

    return RepoSample(
        idx=idx,
        repo_name=_to_str_or_empty(repo_name) or f"unknown_repo_{idx}",
        repo_url=_to_str_or_empty(repo_url),
        commit_id=_to_str_or_empty(commit_id),
        docs_tree=docs_tree,
        structured_docs=structured_docs,
        rubrics=rubrics,
        qa_pairs=_normalize_qa(qa_raw),
        raw=record,
    )


def load_codewikibench(
    split: str,
    max_repos: int | None = None,
    verbose_warnings: bool = True,
) -> list[RepoSample]:
    ds: Dataset = load_dataset("anhnh2002/codewikibench", split=split)

    samples: list[RepoSample] = []
    for idx, row in enumerate(ds):
        sample = normalize_record(idx=idx, record=dict(row), verbose_warnings=verbose_warnings)
        samples.append(sample)
        if max_repos is not None and len(samples) >= max_repos:
            break

    if verbose_warnings and samples:
        first_keys = sorted(samples[0].raw.keys())
        print(f"[dataset-info] Loaded {len(samples)} samples from split='{split}'")
        print(f"[dataset-info] First sample keys: {first_keys}")

    return samples
