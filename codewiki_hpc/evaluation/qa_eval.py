from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.text import split_sentences, token_overlap_score
from .metrics import cosine_similarity, mean


@dataclass
class QAResult:
    score: float
    details: list[dict[str, Any]]


class QAEvaluator:
    def __init__(self, embed_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.embed_model_name = embed_model_name
        self.embedder = None
        try:
            from sentence_transformers import SentenceTransformer

            self.embedder = SentenceTransformer(embed_model_name)
        except Exception:
            self.embedder = None

    def _best_doc_sentence(self, question: str, doc_text: str) -> str:
        sents = split_sentences(doc_text)
        if not sents:
            return ""

        if self.embedder is None:
            best = max(sents, key=lambda s: token_overlap_score(question, s))
            return best

        q_emb = self.embedder.encode([question])[0]
        s_emb = self.embedder.encode(sents)

        best_idx = 0
        best_score = -1.0
        for i, emb in enumerate(s_emb):
            sim = cosine_similarity(q_emb, emb)
            if sim > best_score:
                best_score = sim
                best_idx = i
        return sents[best_idx]

    def _answer_similarity(self, prediction: str, reference: str) -> float:
        if not prediction or not reference:
            return 0.0

        if self.embedder is None:
            return token_overlap_score(prediction, reference)

        pe, re = self.embedder.encode([prediction, reference])
        return cosine_similarity(pe, re)

    def evaluate(self, doc_text: str, qa_pairs: list[dict[str, str]]) -> QAResult:
        if not qa_pairs:
            return QAResult(score=0.0, details=[])

        details: list[dict[str, Any]] = []
        scores: list[float] = []

        for item in qa_pairs:
            q = item.get("question", "")
            ref = item.get("answer", "")
            pred = self._best_doc_sentence(q, doc_text)
            sim = self._answer_similarity(pred, ref)
            scores.append(sim)
            details.append(
                {
                    "question": q,
                    "reference_answer": ref,
                    "predicted_answer": pred,
                    "similarity": sim,
                }
            )

        return QAResult(score=mean(scores), details=details)
