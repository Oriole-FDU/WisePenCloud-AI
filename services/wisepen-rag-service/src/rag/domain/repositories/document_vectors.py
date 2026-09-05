"""文档 Chunk 检索投影的写入与初检候选端口。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from rag.application.document.models import DocChunk
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.metadata_filters import MetadataFilterCondition


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    """文档 Qdrant 初检返回的运行时引用，不是检索对外结果。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    dense_rank: int | None = None
    lexical_rank: int | None = None


class DocumentVectorRepository(Protocol):
    """管理可由 Mongo DocChunk 重建的 Qdrant 文档索引投影。"""

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def write_revision(
        self,
        *,
        chunks: Sequence[DocChunk],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None: ...

    async def is_complete(
        self,
        *,
        resource_id: str,
        content_revision: str,
        chunk_ids: Sequence[str],
    ) -> bool: ...

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        limit: int,
        metadata_filters: Sequence[MetadataFilterCondition] = (),
    ) -> list[VectorCandidate]: ...

    async def search_bm25(
        self,
        *,
        query: str,
        scope: PermissionScope,
        limit: int,
        metadata_filters: Sequence[MetadataFilterCondition] = (),
    ) -> list[VectorCandidate]: ...
