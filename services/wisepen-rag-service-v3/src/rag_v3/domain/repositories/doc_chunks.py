"""DocChunk 权威事实的仓储端口。"""

from collections.abc import Sequence
from typing import Protocol

from rag_v3.domain.models import DocChunk


class DocChunkRepository(Protocol):
    """幂等保存并按 revision 或 Section 批量读取检索原子。"""

    async def save_revision(self, chunks: Sequence[DocChunk]) -> None: ...

    async def get_revision_chunks(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> list[DocChunk]: ...

    async def get_section_chunks(
        self,
        *,
        resource_id: str,
        content_revision: str,
        section_ids: Sequence[str],
    ) -> list[DocChunk]: ...
