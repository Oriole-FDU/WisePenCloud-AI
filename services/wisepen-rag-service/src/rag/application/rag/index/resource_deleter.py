from __future__ import annotations

import asyncio
from collections.abc import Sequence

from rag.domain.repositories import (
    GenerationArtifactStore,
    KnowledgeGraphRepository,
    ResourceAclStore,
    ResourceIndexWriter,
    RetrievalIndexWriter,
)


class ResourceDeleter:
    """先清内容发布指针，再并行清理所有持久化派生数据。"""

    def __init__(
        self,
        *,
        resource_writer: ResourceIndexWriter,
        retrieval_writer: RetrievalIndexWriter,
        graph_repository: KnowledgeGraphRepository,
        generation_artifacts: GenerationArtifactStore,
        acl_store: ResourceAclStore,
    ) -> None:
        self._resource_writer = resource_writer
        self._retrieval_writer = retrieval_writer
        self._graph_repository = graph_repository
        self._generation_artifacts = generation_artifacts
        self._acl_store = acl_store

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        ids = list(dict.fromkeys(resource_ids))
        if not ids:
            return

        # 发布指针先失效，后端并行清理期间 READ/VERIFY 已经无法命中内容。
        await self._resource_writer.clear_resource_states(ids)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._retrieval_writer.delete_resources(ids))
            tasks.create_task(self._graph_repository.delete_resources(ids))
            tasks.create_task(self._resource_writer.delete_resources(ids))
            tasks.create_task(self._generation_artifacts.delete_resources(ids))
            tasks.create_task(self._acl_store.delete_resources(ids))
