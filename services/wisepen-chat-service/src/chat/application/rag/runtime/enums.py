from enum import StrEnum


class RagIndexingStatus(StrEnum):
    """RAG resource indexing lifecycle status."""

    PENDING = "pending"
    INDEXING = "indexing"
    SUCCESS = "success"
    FAILED = "failed"
