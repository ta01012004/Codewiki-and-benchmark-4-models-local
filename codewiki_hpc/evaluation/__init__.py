"""Evaluation helpers for CodeWikiBench-style scoring."""

from .matrix import (
    MATRIX_MODEL_COLUMNS,
    MATRIX_REPO_COLUMNS,
    aggregate_model_matrix,
    compute_quality_score,
    infer_primary_language,
    write_model_matrix_csv,
    write_repo_matrix_csv,
)
from .paper_compare import generate_paper_comparison
from .rubric_eval import RubricEvalResult, evaluate_rubrics, evaluate_rubrics_matrix

__all__ = [
    "MATRIX_MODEL_COLUMNS",
    "MATRIX_REPO_COLUMNS",
    "RubricEvalResult",
    "aggregate_model_matrix",
    "compute_quality_score",
    "evaluate_rubrics",
    "evaluate_rubrics_matrix",
    "generate_paper_comparison",
    "infer_primary_language",
    "write_model_matrix_csv",
    "write_repo_matrix_csv",
]
