from __future__ import annotations

from typing import Any


def rouge_l_score(prediction: str, reference: str) -> float:
    if not prediction.strip() or not reference.strip():
        return 0.0

    try:
        from rouge_score import rouge_scorer

        scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
        scores = scorer.score(reference, prediction)
        return float(scores["rougeL"].fmeasure)
    except Exception:
        # Fallback: token-overlap F1 proxy.
        p_tokens = prediction.lower().split()
        r_tokens = reference.lower().split()
        if not p_tokens or not r_tokens:
            return 0.0
        p_set = set(p_tokens)
        r_set = set(r_tokens)
        inter = len(p_set & r_set)
        prec = inter / max(len(p_set), 1)
        rec = inter / max(len(r_set), 1)
        if prec + rec == 0:
            return 0.0
        return 2 * prec * rec / (prec + rec)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def try_bertscore(predictions: list[str], references: list[str], lang: str = "en") -> float | None:
    try:
        from bert_score import score as bert_score

        _, _, f1 = bert_score(predictions, references, lang=lang, rescale_with_baseline=True)
        return float(f1.mean().item())
    except Exception:
        return None


def cosine_similarity(a: Any, b: Any) -> float:
    import numpy as np

    a = np.array(a)
    b = np.array(b)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
