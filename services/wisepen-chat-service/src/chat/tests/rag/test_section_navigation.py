from __future__ import annotations

import pytest

from chat.application.rag.evidence import (
    RagEvidenceUnavailableError,
    RagMaterializedHit,
    RagMaterializedSource,
)
from chat.application.rag.ingestion import (
    RagSectionNode,
    RagSectionReadingBlock,
    RagSourceRef,
)
from chat.application.rag.retrieval import RagRankedHit, RagRetrievalCandidate
from chat.application.rag.section_navigation import RagSectionNavigator, RagSectionView
from chat.application.utils.chunkers import SourceSpan
from chat.application.utils.ranking import RankCandidate, RankedCandidate


class _SectionRepository:
    def __init__(
        self,
        *,
        views: dict[str, RagSectionView],
        blocks: tuple[RagSectionReadingBlock, ...] = (),
    ) -> None:
        self.views = views
        self.blocks = blocks

    async def load_applied_section_views(self, *, resource_id, section_ids):
        return tuple(
            self.views[section_id]
            for section_id in section_ids
            if section_id in self.views
        )

    async def load_applied_section_reading_blocks(
        self,
        *,
        resource_id,
        section_ids,
    ):
        return tuple(block for block in self.blocks if block.section_id in section_ids)


def _section(
    section_id: str,
    title: str,
    *,
    parent_section_id: str | None = None,
    ordinal: int = 0,
) -> RagSectionNode:
    return RagSectionNode(
        section_id=section_id,
        resource_id="resource-1",
        document_version=1,
        title=title,
        level=1 if parent_section_id is None else 2,
        parent_section_id=parent_section_id,
        ordinal=ordinal,
        section_path=(title,),
        summary=f"{title} summary",
        own_start=ordinal * 10,
        own_end=ordinal * 10 + 10,
        subtree_end=ordinal * 10 + 10,
    )


def _block(block_id: str, section_id: str, ordinal: int = 0) -> RagSectionReadingBlock:
    return RagSectionReadingBlock(
        block_id=block_id,
        section_id=section_id,
        ordinal=ordinal,
        raw_text=f"content:{block_id}",
        source_spans=(SourceSpan(ordinal * 5, ordinal * 5 + 5),),
        page_labels=(),
        anchor_labels=(),
    )


def _materialized_hit() -> RagMaterializedHit:
    source = RagMaterializedSource(
        source_ref=RagSourceRef(
            ref_id="ref-1",
            resource_id="resource-1",
            document_version=1,
            chunk_id="chunk-1",
            section_id="section-current",
            section_path=("Current",),
            source_spans=(SourceSpan(0, 5),),
        ),
        content="evidence",
    )
    candidate = RagRetrievalCandidate(
        chunk_id="chunk-1",
        reading_block_id="block-1",
        section_id="section-current",
        section_path=("Current",),
        resource_id="resource-1",
        document_version=1,
        content_revision="revision-1",
        raw_text="evidence",
        page_labels=(),
        anchor_labels=(),
        source_ref_id="ref-1",
        signals=(),
    )
    return RagMaterializedHit(
        hit=RagRankedHit(
            candidate=candidate,
            ranking=RankedCandidate(
                candidate=RankCandidate(candidate_id="chunk-1"),
                rank=1,
                score=1.0,
            ),
        ),
        reading_block=_block("block-1", "section-current"),
        source=source,
    )


@pytest.mark.asyncio
async def test_hit_is_promoted_to_section_view_with_lightweight_frontier() -> None:
    current = _section("section-current", "Current", parent_section_id="section-root")
    parent = _section("section-root", "Root")
    child = _section("section-child", "Child", parent_section_id="section-current")
    navigator = RagSectionNavigator(
        repository=_SectionRepository(
            views={
                current.section_id: RagSectionView(
                    section=current,
                    parent=parent,
                    children=(child,),
                )
            }
        )
    )

    result = await navigator.build_hits((_materialized_hit(),))

    assert result[0].view.section is current
    assert result[0].view.reading_blocks[0].block_id == "block-1"
    assert result[0].view.sources[0].content == "evidence"
    assert result[0].view.parent is parent
    assert result[0].view.children == (child,)


@pytest.mark.asyncio
async def test_read_sections_returns_all_reading_blocks_in_order() -> None:
    current = _section("section-current", "Current")
    blocks = (
        _block("block-1", current.section_id, 0),
        _block("block-2", current.section_id, 1),
    )
    navigator = RagSectionNavigator(
        repository=_SectionRepository(
            views={current.section_id: RagSectionView(section=current)},
            blocks=blocks,
        )
    )

    result = await navigator.read_sections(
        resource_id="resource-1",
        section_ids=(current.section_id,),
    )

    assert result[0].reading_blocks == blocks


@pytest.mark.asyncio
async def test_navigator_rejects_missing_applied_section() -> None:
    navigator = RagSectionNavigator(repository=_SectionRepository(views={}))

    with pytest.raises(RagEvidenceUnavailableError, match="missing"):
        await navigator.build_hits((_materialized_hit(),))
