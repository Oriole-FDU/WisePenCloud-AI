from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.application.rag.evidence.models import RagMaterializedSource
    from rag.application.rag.graph_extraction.models import KnowledgeExtractionSource
    from rag.application.rag.ingestion.models import RagSectionReadingBlock


class RagKnowledgeExtractionSourceRepository(ABC):
    """图抽取读取当前 applied 正文投影的接口。"""

    @abstractmethod
    async def load_applied_extraction_source(
        self,
        resource_id: str,
    ) -> KnowledgeExtractionSource | None:
        """读取当前 applied revision 的图抽取输入。"""
        pass


class RagSourceRepository(ABC):
    """Applied SourceRef 的权威回源接口。"""

    @abstractmethod
    async def load_applied_sources(
        self,
        *,
        resource_id: str,
        ref_ids: Sequence[str],
    ) -> tuple[RagMaterializedSource, ...]:
        """读取已应用的 SourceRef 原文。"""
        pass

    @abstractmethod
    async def load_applied_reading_blocks(
        self,
        *,
        resource_id: str,
        reading_block_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        """按检索子块引用读取当前 applied Section 阅读块。"""
        pass
