from __future__ import annotations

import re
from pathlib import Path


def read_text_safe(path: str | Path, max_chars: int | None = None) -> str:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if max_chars is not None:
        return text[:max_chars]
    return text


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def token_overlap_score(a: str, b: str) -> float:
    ta = set(normalize_text(a).split())
    tb = set(normalize_text(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta.intersection(tb))
    union = len(ta.union(tb))
    return inter / max(union, 1)


def split_sentences(text: str) -> list[str]:
    out = re.split(r"(?<=[.!?])\s+", text)
    return [x.strip() for x in out if x.strip()]
