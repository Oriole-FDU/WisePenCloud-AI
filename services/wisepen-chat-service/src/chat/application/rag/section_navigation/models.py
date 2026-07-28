from __future__ import annotations

from dataclasses import dataclass

from chat.application.rag.evidence import RagMaterializedHit, RagMaterializedSource
from chat.application.rag.ingestion import RagSectionNode, RagSectionReadingBlock


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


@dataclass(frozen=True, slots=True)
class RagLocatedSection:
    """由一个检索命中提升得到的 Section 结果。"""

    materialized_hit: RagMaterializedHit
    view: RagSectionView
