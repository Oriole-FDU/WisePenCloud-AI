class RagError(Exception):
    """Base error for the RAG application facade."""


class RagInvalidResourceKindError(RagError):
    """Raised when an external resource_kind is not a supported public value."""


class RagResourceNotFoundError(RagError):
    """Raised when a user-scoped RAG resource does not exist."""
