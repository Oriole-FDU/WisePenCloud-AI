"""检索索引写入能力的领域 port。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from rag.domain.models.acl import ResourceAcl
from rag.domain.models.provenance import SourceRef
from rag.domain.models.retrieval import RetrievalChunk


class RetrievalIndexWriter(Protocol):
    """管理 RetrievalChunk 在外部检索索引中的 revision 生命周期。"""

    async def load_reusable_vectors(
        self,
        *,
        resource_id: str,
        chunks: Sequence[RetrievalChunk],
    ) -> Mapping[str, Sequence[float]]: ...

    async def write_staged_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        chunks: Sequence[RetrievalChunk],
        source_refs: Sequence[SourceRef],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None: ...

    async def activate_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> None: ...

    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...
