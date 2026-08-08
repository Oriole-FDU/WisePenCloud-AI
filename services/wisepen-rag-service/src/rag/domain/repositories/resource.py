from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.application.rag.resource_snapshot import (
        RagResourceContentReadResult,
        RagResourceSnapshot,
    )


class RagResourceSnapshotRepository(ABC):
    """资源副本的文档结构与按 page/section 读取接口。"""

    @abstractmethod
    async def load_applied_resource_snapshot(
        self,
        *,
        resource_id: str,
    ) -> RagResourceSnapshot | None:
        """读取当前 applied revision 的文档结构。"""
        pass

    @abstractmethod
    async def read_applied_page_content(
        self,
        *,
        resource_id: str,
        page_labels: Sequence[str],
    ) -> RagResourceContentReadResult | None:
        """按页标签批量读取当前 applied revision 的正文窗口。"""
        pass

    @abstractmethod
    async def read_applied_section_content(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> RagResourceContentReadResult | None:
        """按 Section ID 批量读取当前 applied ReadingBlock。"""
        pass
