from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ..utils.text import normalize_text
from .metrics import mean


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


def _keyword_candidates(texts: list[str]) -> list[str]:
    joined = " ".join(texts).lower()
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", joined)
    bad = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "code",
        "repo",
        "repository",
        "module",
        "file",
    }
    uniq = []
    seen = set()
    for w in words:
        if w in bad:
            continue
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq[:120]


def _collect_leaf_items(x: Any, path: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    if x is None:
        return leaves
    if isinstance(x, dict):
        if not x:
            return leaves
        for k, v in x.items():
            leaves.extend(_collect_leaf_items(v, path + (str(k),)))
        return leaves
    if isinstance(x, list):
        if not x:
            return leaves
        for i, item in enumerate(x):
            leaves.extend(_collect_leaf_items(item, path + (f"[{i}]",)))
        return leaves
    text = str(x).strip()
    if text:
        leaves.append({"path": " > ".join(path), "text": text})
    return leaves


def _is_actionable_line(line: str) -> bool:
    l = line.strip().lower()
    if not l:
        return False
    if l.startswith("- "):
        l = l[2:].strip()
    tokens = (
        "run ",
        "execute ",
        "install ",
        "set ",
        "configure ",
        "build ",
        "test ",
        "python ",
        "pip ",
        "make ",
        "docker ",
        "git ",
    )
    return any(tok in f"{l} " for tok in tokens)


def _factual_grounding_score(doc_text: str) -> float:
    lines = [ln.strip() for ln in doc_text.splitlines() if ln.strip()]
    if not lines:
        return 0.0

    with_digits = sum(1 for ln in lines if re.search(r"\d", ln))
    with_code = sum(1 for ln in lines if "`" in ln or "/" in ln or ".py" in ln or ".js" in ln)
    with_cmd = sum(1 for ln in lines if ln.startswith("```") or _is_actionable_line(ln))
    ratio = (0.45 * (with_digits / len(lines))) + (0.35 * (with_code / len(lines))) + (0.2 * (with_cmd / len(lines)))
    return max(0.0, min(1.0, ratio * 2.2))


def _coherence_score(doc_text: str) -> float:
    lines = [ln.strip() for ln in doc_text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    headers = [ln for ln in lines if ln.startswith("#")]
    bullet_lines = [ln for ln in lines if ln.startswith("- ") or re.match(r"^\d+\.\s", ln)]
    avg_len = sum(len(ln.split()) for ln in lines) / len(lines)

    header_ratio = min(1.0, len(headers) / 6.0)
    bullet_ratio = min(1.0, len(bullet_lines) / 18.0)
    length_quality = 1.0 - min(1.0, abs(avg_len - 18.0) / 22.0)
    return max(0.0, min(1.0, 0.45 * header_ratio + 0.25 * bullet_ratio + 0.30 * length_quality))


@dataclass
class RubricEvalResult:
    overall_score: float
    hierarchy_alignment: float
    leaf_coverage: float
    structural_fidelity: float
    factual_grounding: float
    actionability: float
    coherence: float
    required_sections_coverage: float
    key_term_coverage: float
    leaf_items_total: int
    leaf_items_hit: int
    notes: str


def evaluate_rubrics_matrix(
    doc_text: str,
    rubrics: Any,
    docs_tree: Any,
    structured_docs: Any,
) -> RubricEvalResult:
    if not doc_text.strip():
        return RubricEvalResult(
            overall_score=0.0,
            hierarchy_alignment=0.0,
            leaf_coverage=0.0,
            structural_fidelity=0.0,
            factual_grounding=0.0,
            actionability=0.0,
            coherence=0.0,
            required_sections_coverage=0.0,
            key_term_coverage=0.0,
            leaf_items_total=0,
            leaf_items_hit=0,
            notes="empty_document",
        )

    normalized_doc = normalize_text(doc_text)

    required_sections = [
        "overview",
        "architecture",
        "data flow",
        "execution flow",
        "configuration",
        "dependencies",
        "how to run",
        "extension",
    ]
    section_hits = [1.0 if sec in normalized_doc else 0.0 for sec in required_sections]
    section_coverage = mean(section_hits)

    rubric_texts = _flatten_text_chunks(rubrics)
    tree_texts = _flatten_text_chunks(docs_tree)
    struct_texts = _flatten_text_chunks(structured_docs)

    key_terms = _keyword_candidates(rubric_texts + tree_texts + struct_texts)
    mentions = [1.0 if term in normalized_doc else 0.0 for term in key_terms] if key_terms else []
    key_term_coverage = mean(mentions) if mentions else 0.0

    leaf_items = _collect_leaf_items(rubrics) or _collect_leaf_items(structured_docs)
    leaf_total = len(leaf_items)
    leaf_hit = 0
    for item in leaf_items:
        item_text = normalize_text(item["text"])
        if not item_text:
            continue
        terms = _keyword_candidates([item_text])[:5]
        if not terms:
            continue
        if any(t in normalized_doc for t in terms):
            leaf_hit += 1
    leaf_coverage = (leaf_hit / leaf_total) if leaf_total > 0 else 0.0

    hierarchy_alignment = 0.6 * leaf_coverage + 0.4 * section_coverage
    structural_fidelity = 0.6 * section_coverage + 0.4 * key_term_coverage

    actionable_lines = [ln for ln in doc_text.splitlines() if _is_actionable_line(ln)]
    actionability = min(1.0, len(actionable_lines) / 6.0)

    factual_grounding = _factual_grounding_score(doc_text)
    coherence = _coherence_score(doc_text)

    overall = (
        0.30 * hierarchy_alignment
        + 0.20 * structural_fidelity
        + 0.20 * factual_grounding
        + 0.15 * actionability
        + 0.15 * coherence
    )

    notes = (
        f"hierarchy={hierarchy_alignment:.3f}; leaf_coverage={leaf_coverage:.3f}; "
        f"structural={structural_fidelity:.3f}; factual={factual_grounding:.3f}; "
        f"actionability={actionability:.3f}; coherence={coherence:.3f}; "
        f"section_coverage={section_coverage:.3f}; key_term_coverage={key_term_coverage:.3f}; "
        f"leaf_hits={leaf_hit}/{leaf_total}; terms={len(key_terms)}"
    )

    return RubricEvalResult(
        overall_score=max(0.0, min(1.0, overall)),
        hierarchy_alignment=max(0.0, min(1.0, hierarchy_alignment)),
        leaf_coverage=max(0.0, min(1.0, leaf_coverage)),
        structural_fidelity=max(0.0, min(1.0, structural_fidelity)),
        factual_grounding=max(0.0, min(1.0, factual_grounding)),
        actionability=max(0.0, min(1.0, actionability)),
        coherence=max(0.0, min(1.0, coherence)),
        required_sections_coverage=max(0.0, min(1.0, section_coverage)),
        key_term_coverage=max(0.0, min(1.0, key_term_coverage)),
        leaf_items_total=leaf_total,
        leaf_items_hit=leaf_hit,
        notes=notes,
    )


def evaluate_rubrics(
    doc_text: str,
    rubrics: Any,
    docs_tree: Any,
    structured_docs: Any,
) -> tuple[float, str]:
    result = evaluate_rubrics_matrix(
        doc_text=doc_text,
        rubrics=rubrics,
        docs_tree=docs_tree,
        structured_docs=structured_docs,
    )
    return result.overall_score, result.notes
