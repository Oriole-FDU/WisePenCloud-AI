from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from chat.application.rag.acl import RagPermissionAuthorizer
from chat.application.rag.repositories import RagSourceRepository
from chat.application.rag.retrieval import RagPermissionScope, RagRankedHit

from .models import RagMaterializedHit, RagMaterializedSource


class RagEvidenceUnavailableError(RuntimeError):
    """Applied retrieval hit 无法从权威 SourceRef 完整回源。"""


class RagEvidenceMaterializer:
    """将检索命中回源为经过最终权限校验的权威证据。"""

    __slots__ = ("_permission_authorizer", "_repository")

    def __init__(self, *, repository: RagSourceRepository, permission_authorizer: RagPermissionAuthorizer) -> None:
        self._repository = repository
        self._permission_authorizer = permission_authorizer

    async def materialize(
        self, hits: tuple[RagRankedHit, ...], permission_scope: RagPermissionScope
    ) -> tuple[RagMaterializedHit, ...]:
        """批量回源检索命中，并恢复每个命中关联的完整证据。"""
        if not hits:
            return ()

        # 按资源聚合 SourceRef 和 ReadingBlock，减少仓储查询次数。
        ref_ids_by_resource: dict[str, list[str]] = {}
        block_ids_by_resource: dict[str, list[str]] = {}

        for hit in hits:
            ref_ids_by_resource.setdefault(hit.candidate.resource_id, []).append(
                hit.candidate.source_ref_id
            )
            block_ids_by_resource.setdefault(hit.candidate.resource_id, []).append(
                hit.candidate.reading_block_id
            )

        # SourceRef 回源内部已经做了最终 ACL 校验；这里只负责 ReadingBlock 加载。
        sources = await self.materialize_refs(ref_ids_by_resource, permission_scope)
        sources_by_key = {
            (source.source_ref.resource_id, source.source_ref.ref_id): source for source in sources
        }

        # ReadingBlock 按资源分组并行加载，避免对同一资源重复往返。
        block_groups = await asyncio.gather(
            *(
                self._repository.load_applied_reading_blocks(
                    resource_id=resource_id,
                    reading_block_ids=tuple(dict.fromkeys(block_ids)),
                )
                for resource_id, block_ids in block_ids_by_resource.items()
            )
        )
        blocks_by_key = {
            (resource_id, block.block_id): block
            for resource_id, blocks in zip(
                block_ids_by_resource,
                block_groups,
                strict=True,
            )
            for block in blocks
        }

        materialized_hits: list[RagMaterializedHit] = []
        # 同一 Section 的多个检索子块只保留首个（已按 ranking 排好序），与 README 的不变量对齐。
        materialized_section_keys: set[tuple[str, str]] = set()
        for hit in hits:
            block_key = (
                hit.candidate.resource_id,
                hit.candidate.reading_block_id,
            )
            reading_block = blocks_by_key.get(block_key)
            if reading_block is None:
                raise RagEvidenceUnavailableError(
                    "applied reading block is missing: " f"{block_key[0]}/{block_key[1]}"
                )
            section_key = (hit.candidate.resource_id, hit.candidate.section_id)
            if section_key in materialized_section_keys:
                continue
            materialized_section_keys.add(section_key)

            materialized_hits.append(
                RagMaterializedHit(
                    hit=hit,
                    reading_block=reading_block,
                    source=sources_by_key[
                        (hit.candidate.resource_id, hit.candidate.source_ref_id)
                    ],
                )
            )

        return tuple(materialized_hits)

    async def materialize_refs(
        self,
        ref_ids_by_resource: Mapping[str, Sequence[str]],
        permission_scope: RagPermissionScope,
    ) -> tuple[RagMaterializedSource, ...]:
        """回源 Applied SourceRef，并执行完整性与最终权限校验。"""
        if not ref_ids_by_resource:
            return ()

        resource_ids = tuple(ref_ids_by_resource)

        # 单个资源内部去重，不改变 SourceRef 的首次出现顺序。
        unique_ref_ids_by_resource = {
            resource_id: tuple(dict.fromkeys(ref_ids_by_resource[resource_id]))
            for resource_id in resource_ids
        }

        # 不同资源的 SourceRef 可以并行回源。
        loaded_groups = await asyncio.gather(
            *(
                self._repository.load_applied_sources(
                    resource_id=resource_id,
                    ref_ids=unique_ref_ids_by_resource[resource_id],
                )
                for resource_id in resource_ids
            )
        )

        sources_by_key = {
            (source.source_ref.resource_id, source.source_ref.ref_id): source
            for group in loaded_groups
            for source in group
        }

        # 保持调用方传入的资源顺序和各资源内部的 SourceRef 顺序。
        requested_refs = tuple(
            (resource_id, ref_id)
            for resource_id in resource_ids
            for ref_id in unique_ref_ids_by_resource[resource_id]
        )

        # Applied SourceRef 必须全部存在，不允许返回不完整证据。
        missing_refs = tuple(
            source_key for source_key in requested_refs if source_key not in sources_by_key
        )

        if missing_refs:
            raise RagEvidenceUnavailableError(
                "applied source refs are missing: "
                + ", ".join(f"{resource_id}/{ref_id}" for resource_id, ref_id in missing_refs)
            )

        # 回源完成后再次读取本地权威 ACL，作为证据返回前的最终授权门。
        accessible_resource_ids = await self._permission_authorizer.accessible_resource_ids(
            resource_ids=resource_ids, scope=permission_scope
        )

        inaccessible_resource_ids = tuple(
            resource_id for resource_id in resource_ids if resource_id not in accessible_resource_ids
        )
        if inaccessible_resource_ids:
            raise RagEvidenceUnavailableError(
                "source permission changed before materialization: "
                + ", ".join(inaccessible_resource_ids)
            )

        return tuple(sources_by_key[source_key] for source_key in requested_refs)
