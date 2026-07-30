from __future__ import annotations

import asyncio
from dataclasses import replace

from chat.application.rag.evidence import (
    RagEvidenceUnavailableError,
    RagMaterializedHit,
    RagMaterializedSource,
)
from chat.application.rag.ingestion.models import RagSectionReadingBlock
from chat.application.rag.repositories import RagSectionNavigationRepository
from .models import RagSectionView


class RagSectionNavigator:
    """将证据提升为可继续展开的标题树节点。"""

    __slots__ = ("_repository",)

    def __init__(self, *, repository: RagSectionNavigationRepository) -> None:
        self._repository = repository

    async def build_hits(
            self,
            hits: tuple[RagMaterializedHit, ...],
    ) -> tuple[RagSectionView, ...]:
        """把检索命中提升为 SectionView，每个 Section 保留 ranking 最高的命中。"""
        # 按 (resource_id, section_id) 批量加载 Section 视图（含轻量 frontier）。
        views = await self._load_views(
            tuple(
                (hit.resource_id, hit.section_id)
                for hit in hits
            )
        )
        return tuple(
            replace(
                views[(hit.resource_id, hit.section_id)],
                sources=(hit.source,),
                reading_blocks=(hit.reading_block,),
            )
            for hit in hits
        )

    async def build_sources(
            self,
            sources: tuple[RagMaterializedSource, ...],
    ) -> tuple[RagSectionView, ...]:
        """直接为 SourceRef 构建 SectionView，不经过候选排序。"""
        # 去重：多个 SourceRef 可能映射到同一 Section，只需为每个 Section 加载一次视图。
        keys = tuple(
            dict.fromkeys(
                (source.source_ref.resource_id, source.source_ref.section_id)
                for source in sources
            )
        )
        views = await self._load_views(keys)
        return tuple(
            replace(
                views[key],
                sources=tuple(
                    source
                    for source in sources
                    if (source.source_ref.resource_id, source.source_ref.section_id) == key
                ),
            )
            for key in keys
        )

    async def read_sections(
            self,
            *,
            resource_id: str,
            section_ids: tuple[str, ...],
    ) -> tuple[RagSectionView, ...]:
        """读取已发现 Section 的全部 ReadingBlock，用于返回完整正文。"""
        keys = tuple((resource_id, section_id) for section_id in section_ids)
        # 并行加载：视图（结构信息）与 ReadingBlock（正文）可以同时读取。
        views, reading_blocks = await asyncio.gather(
            self._load_views(keys),
            self._repository.load_applied_section_reading_blocks(
                resource_id=resource_id,
                section_ids=section_ids,
            ),
        )
        # 把 ReadingBlock 按 Section 聚合；同一 Section 可能跨多个块。
        blocks_by_section: dict[str, list[RagSectionReadingBlock]] = {}
        for block in reading_blocks:
            blocks_by_section.setdefault(block.section_id, []).append(block)
        return tuple(
            replace(
                views[(resource_id, section_id)],
                reading_blocks=tuple(blocks_by_section.get(section_id, ())),
            )
            for section_id in section_ids
        )

    async def _load_views(
            self,
            keys: tuple[tuple[str, str], ...],
    ) -> dict[tuple[str, str], RagSectionView]:
        """按资源并行加载 Section 视图，并对缺失的 Section 立即报错。"""
        # 输入可能有重复键，去重并保留顺序。
        keys = tuple(dict.fromkeys(keys))
        # 按资源分组：仓储层可以一次读多个 Section，减少往返。
        resource_sections: dict[str, list[str]] = {}
        for resource_id, section_id in keys:
            resource_sections.setdefault(resource_id, []).append(section_id)

        groups = await asyncio.gather(
            *(
                self._repository.load_applied_section_views(
                    resource_id=resource_id,
                    section_ids=tuple(section_ids),
                )
                for resource_id, section_ids in resource_sections.items()
            )
        )
        views = {
            (view.section.resource_id, view.section.section_id): view
            for group in groups
            for view in group
        }
        # Section 必须全部存在；任何缺失都说明 evidence 链路不一致。
        missing = tuple(key for key in keys if key not in views)
        if missing:
            raise RagEvidenceUnavailableError(
                "applied sections are missing: "
                + ", ".join(f"{resource_id}/{section_id}" for resource_id, section_id in missing)
            )
        return views
