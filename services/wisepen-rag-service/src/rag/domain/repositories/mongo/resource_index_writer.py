"""index 写入能力需要的资源索引仓储契约。"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from common.utils.document import OutlineNode

from rag.domain.models.content import ContentRevision, ReadingBlock
from rag.domain.models.provenance import SourceRef
from rag.domain.models.structure import DocumentStructure


class StageAction(StrEnum):
    STAGED = "staged"
    ALREADY_APPLIED = "already_applied"
    STALE = "stale"


class ResourceIndexWriter(Protocol):
    """写入并发布资源索引 revision，不对外提供内容读取。"""

    async def stage_revision(
        self,
        revision: ContentRevision,
        markdown: str,
        structure: DocumentStructure,
        outline: list[OutlineNode],
        reading_blocks: Sequence[ReadingBlock],
        source_refs: Sequence[SourceRef],
    ) -> StageAction: ...

    async def apply_revision(self, revision: ContentRevision) -> None: ...

    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None: ...

    async def clear_resource_states(self, resource_ids: Sequence[str]) -> None: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...
