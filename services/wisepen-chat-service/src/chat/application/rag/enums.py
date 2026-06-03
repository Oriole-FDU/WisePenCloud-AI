from enum import StrEnum


class ResourceKind(StrEnum):

    NOTE = "note"
    DOCUMENT = "document"


class RetrievalMode(StrEnum):

    NORMAL = "normal"
    SEMANTIC = "semantic"
    EXACT = "exact"
