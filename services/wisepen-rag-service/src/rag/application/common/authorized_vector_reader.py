"""在向量存储应用候选预算前加入本次资源快照的可见版本条件。"""

from dataclasses import replace

from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.filters import FilterOperator, MetadataFilter, MetadataFilters
from rag.domain.storage.vector import VectorMatch, VectorQuery, VectorReader


class AuthorizedVectorReader(VectorReader):
    """把动态 ACL/发布决定与调用方 metadata 条件取交集后再查询向量库。"""

    def __init__(
        self,
        *,
        reader: VectorReader,
        snapshot_reader: ResourceSnapshotReader,
        snapshot: ResourceSnapshot,
    ) -> None:
        self._reader = reader
        self._snapshot_reader = snapshot_reader
        self._snapshot = snapshot

    async def query(self, request: VectorQuery) -> list[VectorMatch]:
        revisions = await self._snapshot_reader.authorized_content_revisions(
            snapshot=self._snapshot
        )
        if not revisions:
            return []

        authorized = MetadataFilters(
            [MetadataFilter("content_revision", revisions, FilterOperator.IN)]
        )
        filters = (
            authorized
            if request.filters is None
            else MetadataFilters([authorized, request.filters])
        )
        return await self._reader.query(replace(request, filters=filters))
