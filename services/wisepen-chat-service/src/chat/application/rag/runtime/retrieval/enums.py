from enum import StrEnum


class RetrievalChannel(StrEnum):
    """Retrieval channel."""

    DENSE_SEMANTIC = "dense_semantic"
    SPARSE_LEXICAL = "sparse_lexical"
    KEYWORD_EXACT = "keyword_exact"


class RetrievalChannelStatus(StrEnum):
    """Retrieval channel execution status."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class InsufficientReason(StrEnum):
    """Reason why retrieval evidence is insufficient."""

    NO_RESULTS = "no_results"
    LOW_SCORE = "low_score"
    EXACT_MODE_NO_KEYWORD_HIT = "exact_mode_no_keyword_hit"


class RagRecommendedNextAction(StrEnum):
    """Recommended next action after RAG retrieval."""

    ANSWER_WITH_EVIDENCE = "answer_with_evidence"
    REWRITE_QUERY = "rewrite_query"
    USE_WEB_SEARCH = "use_web_search"
    ASK_USER_TO_UPLOAD_OR_INDEX = "ask_user_to_upload_or_index"


class NeighborRelation(StrEnum):
    """Neighbor relation to the main evidence chunk."""

    BEFORE = "before"
    AFTER = "after"
