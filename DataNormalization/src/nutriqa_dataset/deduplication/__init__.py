"""Duplicate-question candidate generation and review application."""

from .workflow import (
    DeduplicationError,
    DeduplicationPreview,
    DeduplicationResult,
    apply_review,
    create_review,
)

__all__ = [
    "DeduplicationError",
    "DeduplicationPreview",
    "DeduplicationResult",
    "apply_review",
    "create_review",
]
