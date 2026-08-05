from __future__ import annotations

from dataclasses import dataclass

from rag.application.rag.evidence import RagMaterializedSource
from rag.application.rag.ingestion import RagSectionNode, RagSectionReadingBlock


@dataclass(frozen=True, slots=True)
class RagSectionView:
    """Agent 可读取并继续展开的单个标题树节点。"""

    section: RagSectionNode
    sources: tuple[RagMaterializedSource, ...] = ()
    reading_blocks: tuple[RagSectionReadingBlock, ...] = ()
    parent: RagSectionNode | None = None
    previous: RagSectionNode | None = None
    next: RagSectionNode | None = None
    children: tuple[RagSectionNode, ...] = ()
