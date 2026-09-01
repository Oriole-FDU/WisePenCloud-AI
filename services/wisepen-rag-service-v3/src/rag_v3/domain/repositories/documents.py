"""权威文档 revision 的仓储端口。"""

from collections.abc import Sequence
from typing import Protocol

from rag_v3.domain.models import Document


class DocumentRepository(Protocol):
    """保存、批量读取 Document 事实并按 Section ID 定位候选 revision。"""

    async def save_revision(self, document: Document) -> None: ...

    async def exists(self, *, resource_id: str, content_revision: str) -> bool: ...

    async def get_revisions(
        self,
        revisions: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], Document]: ...

    async def find_by_section_ids(
        self,
        section_ids: Sequence[str],
    ) -> list[Document]: ...
