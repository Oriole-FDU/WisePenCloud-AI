from dataclasses import dataclass

from chat.application.rag.enums import ResourceKind


@dataclass(frozen=True, slots=True)
class RagIndexMessage:
    """Index queue message."""

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    expected_version: int
    pipeline_version: str
    target_index_version: str
    priority: int = 100
