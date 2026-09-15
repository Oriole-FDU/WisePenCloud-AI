"""Kafka 事件到现有 application 用例的薄编排。"""

from collections.abc import Mapping
from typing import Any

from rag.application.document.indexing import DocumentIndexBuilder
from rag.application.document.models import ContentRevision
from rag.application.document.preparation import DocumentPreparer
from rag.application.publication import AclSynchronizer, DocumentPublication
from rag.domain.repositories.graph_edge_vectors import GraphEdgeVectorRepository
from rag.domain.repositories.graph_node_vectors import GraphNodeVectorRepository
from rag.domain.repositories.graph_topology import GraphTopologyRepository
from rag.domain.repositories.index_state import StageAction


class DocumentReadyHandler:
    """把上游 document-ready 事件接到文档事实、索引和 active 发布链路。"""

    def __init__(
        self,
        *,
        preparer: DocumentPreparer,
        index_builder: DocumentIndexBuilder,
    ) -> None:
        self._preparer = preparer
        self._index_builder = index_builder

    async def handle(self, payload: Mapping[str, Any]) -> None:
        revision = ContentRevision.create(
            resource_id=payload["resource_id"],
            document_version=payload["version"],
            raw_content=payload["content"],
        )
        action = await self._preparer.prepare(
            resource_id=revision.resource_id,
            document_version=revision.document_version,
            markdown=payload["content"],
        )
        if action is StageAction.STALE:
            return
        await self._index_builder.build_and_publish(revision)


class AclRecalculateHandler:
    """把 ACL 更新事件交给现有 authoritative ACL 同步用例。"""

    def __init__(self, *, synchronizer: AclSynchronizer) -> None:
        self._synchronizer = synchronizer

    async def handle(self, payload: Mapping[str, Any]) -> None:
        await self._synchronizer.synchronize([payload["resource_id"]])


class ResourceDestroyHandler:
    """先撤销可见性，再清理已有可重建投影。"""

    def __init__(
        self,
        *,
        publication: DocumentPublication,
        document_vectors,
        node_vectors: GraphNodeVectorRepository,
        edge_vectors: GraphEdgeVectorRepository,
        topology: GraphTopologyRepository | None,
        graph_enabled: bool,
    ) -> None:
        self._publication = publication
        self._document_vectors = document_vectors
        self._node_vectors = node_vectors
        self._edge_vectors = edge_vectors
        self._topology = topology
        self._graph_enabled = graph_enabled

    async def handle(self, payload: Mapping[str, Any]) -> None:
        resource_ids = payload["resource_ids"]
        await self._publication.clear_resources(resource_ids)
        await self._document_vectors.delete_resources(resource_ids)
        if not self._graph_enabled:
            return
        await self._node_vectors.delete_resources(resource_ids)
        await self._edge_vectors.delete_resources(resource_ids)
        if self._topology is not None:
            await self._topology.delete_resources(resource_ids)
