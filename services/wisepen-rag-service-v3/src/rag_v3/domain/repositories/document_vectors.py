"""文档 Chunk 检索投影的写入与完整性校验端口。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from rag_v3.domain.acl import ResourceAcl
from rag_v3.domain.models import DocChunk


class DocumentVectorRepository(Protocol):
    """管理可由 Mongo DocChunk 重建的 Qdrant 文档索引投影。"""

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
