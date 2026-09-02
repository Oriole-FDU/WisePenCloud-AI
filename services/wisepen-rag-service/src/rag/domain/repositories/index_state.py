"""active 指针的发布协调仓储端口。"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Protocol

from rag.application.document.models import ContentRevision, ResourceIndexState


class StageAction(StrEnum):
    STAGED = "staged"
    ALREADY_APPLIED = "already_applied"
    STALE = "stale"


class ResourceIndexStateRepository(Protocol):
    """维护 staged/applied 指针，不负责写入文档正文。"""

    async def stage_revision(
        self,
        revision: ContentRevision,
        *,
        expected_applied_content_revision: str | None,
    ) -> StageAction: ...

    async def apply_revision(self, revision: ContentRevision) -> None: ...

    async def get_states(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceIndexState]: ...

    async def clear_visibility(self, resource_ids: Sequence[str]) -> None: ...
