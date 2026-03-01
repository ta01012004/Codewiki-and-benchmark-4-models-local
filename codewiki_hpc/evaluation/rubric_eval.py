from __future__ import annotations

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


def evaluate_rubrics(
    doc_text: str,
    rubrics: Any,
    docs_tree: Any,
    structured_docs: Any,
) -> tuple[float, str]:
    if not doc_text.strip():
        return 0.0, "empty_document"

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

    rubric_texts = _flatten_text_chunks(rubrics)
    tree_texts = _flatten_text_chunks(docs_tree)
    struct_texts = _flatten_text_chunks(structured_docs)

    key_terms = _keyword_candidates(rubric_texts + tree_texts + struct_texts)
    if key_terms:
        mentions = [1.0 if term.lower() in normalized_doc else 0.0 for term in key_terms]
        key_term_coverage = mean(mentions)
    else:
        key_term_coverage = 0.0

    coverage_score = 0.55 * mean(section_hits) + 0.45 * key_term_coverage
    notes = f"section_coverage={mean(section_hits):.3f}; key_term_coverage={key_term_coverage:.3f}; terms={len(key_terms)}"
    return coverage_score, notes
