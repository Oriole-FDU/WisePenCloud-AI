"""将 Mongo 图谱事实独立投影到 Neo4j 与两个 Qdrant collection。"""

from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI

from rag_v3.domain.graph import (
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
    GraphRevisionFacts,
    graph_source_projection_id,
)
from rag_v3.domain.models import GeneralDocumentMetadata
from rag_v3.domain.repositories.acl import ResourceAclRepository
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository
from rag_v3.domain.repositories.documents import DocumentRepository
from rag_v3.domain.repositories.graph import GraphFactRepository
from rag_v3.domain.repositories.graph_projections import (
    GraphEdgeVectorRepository,
    GraphNodeVectorRepository,
    GraphTopologyRepository,
)
from rag_v3.domain.repositories.index_state import ResourceIndexStateRepository


@dataclass(frozen=True, slots=True)
class GraphProjectionResult:
    """一次图谱投影的实际产出；关闭全局开关时明确返回 skipped。"""

    resource_id: str
    content_revision: str | None
    node_count: int
    edge_count: int
    skipped: bool = False


class GraphProjectionBuilder:
    """重建 active revision 的三个图谱投影，不参与文档发布。"""

    def __init__(
        self,
        *,
        enabled: bool,
        documents: DocumentRepository,
        doc_chunks: DocChunkRepository,
        graph_facts: GraphFactRepository,
        resource_acls: ResourceAclRepository,
        index_states: ResourceIndexStateRepository,
        topology: GraphTopologyRepository | None,
        node_vectors: GraphNodeVectorRepository,
        edge_vectors: GraphEdgeVectorRepository,
        openai_client: AsyncOpenAI,
        embedding_model: str,
        embedding_dimensions: int,
    ) -> None:
        if embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be positive")
        self._enabled = enabled
        self._documents = documents
        self._doc_chunks = doc_chunks
        self._graph_facts = graph_facts
        self._resource_acls = resource_acls
        self._index_states = index_states
        self._topology = topology
        self._node_vectors = node_vectors
        self._edge_vectors = edge_vectors
        self._openai_client = openai_client
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

    async def rebuild(
        self,
        *,
        resource_id: str,
        content_revision: str | None = None,
    ) -> GraphProjectionResult:
        """只重建当前 active revision；旧 revision 绝不写入外部可查询投影。"""
        if not self._enabled:
            return GraphProjectionResult(resource_id, None, 0, 0, skipped=True)
        if self._topology is None:
            raise RuntimeError("graph topology repository is not configured")

        target_revision = await self._require_active_revision(
            resource_id=resource_id,
            content_revision=content_revision,
        )
        documents = await self._documents.get_revisions([(resource_id, target_revision)])
        document = documents.get((resource_id, target_revision))
        if document is None:
            raise ValueError("active document is missing")
        # 通用文档没有默认 Ontology；误调用投影也不能创建空的外部图索引。
        if isinstance(document.metadata, GeneralDocumentMetadata):
            return GraphProjectionResult(resource_id, target_revision, 0, 0, skipped=True)
        resource_acl = (
            await self._resource_acls.get_resource_acls([resource_id])
        ).get(resource_id)
        if resource_acl is None:
            raise PermissionError("resource ACL is missing")
        facts = await self._graph_facts.get_revision_facts(
            resource_id=resource_id,
            content_revision=target_revision,
        )
        chunks = await self._doc_chunks.get_revision_chunks(
            resource_id=resource_id,
            content_revision=target_revision,
        )
        _validate_evidence_chunks(facts, {chunk.chunk_id for chunk in chunks})
        nodes_by_id = {item.node.node_id: item.node for item in facts.nodes}
        _validate_edge_endpoints(facts.edges, nodes_by_id)

        node_texts = {
            _node_projection_id(item): _node_index_text(item.node) for item in facts.nodes
        }
        edge_texts = {
            _edge_projection_id(item): _edge_index_text(item, nodes_by_id)
            for item in facts.edges
        }
        dense_vectors = await self._embed([*node_texts.values(), *edge_texts.values()])
        node_vectors = {
            projection_id: dense_vectors[index]
            for index, projection_id in enumerate(node_texts)
        }
        edge_vectors = {
            projection_id: dense_vectors[index + len(node_texts)]
            for index, projection_id in enumerate(edge_texts)
        }

        # 每个外部目标写入前都重新确认 active，避免新 revision 已发布后继续污染旧投影。
        await self._require_active_revision(
            resource_id=resource_id,
            content_revision=target_revision,
        )
        await self._topology.replace_revision(
            resource_id=resource_id,
            content_revision=target_revision,
            nodes=facts.nodes,
            edges=facts.edges,
            resource_acl=resource_acl,
        )
        await self._require_active_revision(
            resource_id=resource_id,
            content_revision=target_revision,
        )
        await self._node_vectors.replace_revision(
            resource_id=resource_id,
            content_revision=target_revision,
            nodes=facts.nodes,
            dense_vectors=node_vectors,
            resource_acl=resource_acl,
        )
        await self._require_active_revision(
            resource_id=resource_id,
            content_revision=target_revision,
        )
        await self._edge_vectors.replace_revision(
            resource_id=resource_id,
            content_revision=target_revision,
            edges=facts.edges,
            dense_vectors=edge_vectors,
            lexical_texts=edge_texts,
            resource_acl=resource_acl,
        )
        return GraphProjectionResult(
            resource_id=resource_id,
            content_revision=target_revision,
            node_count=len(facts.nodes),
            edge_count=len(facts.edges),
        )

    async def _require_active_revision(
        self,
        *,
        resource_id: str,
        content_revision: str | None,
    ) -> str:
        state = (await self._index_states.get_states([resource_id])).get(resource_id)
        if state is None or state.applied_content_revision is None:
            raise ValueError("resource has no active revision")
        if content_revision is not None and content_revision != state.applied_content_revision:
            raise ValueError("requested graph projection revision is not active")
        return state.applied_content_revision

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._openai_client.embeddings.create(
            model=self._embedding_model,
            input=texts,
            dimensions=self._embedding_dimensions,
        )
        vectors = [item.embedding for item in response.data]
        if len(vectors) != len(texts) or any(
            len(vector) != self._embedding_dimensions for vector in vectors
        ):
            raise ValueError("embedding response dimensions do not match settings")
        return vectors


def _validate_evidence_chunks(facts: GraphRevisionFacts, chunk_ids: set[str]) -> None:
    """P2-A 已校验证据；此处只批量确认其 Chunk 仍是同一 revision 的事实。"""
    missing = {evidence.chunk_id for evidence in facts.evidences} - chunk_ids
    if missing:
        raise ValueError("graph evidence chunk is missing from active revision")


def _validate_edge_endpoints(
    edges: tuple[GraphEdgeProjection, ...],
    nodes_by_id: dict[str, GraphNode],
) -> None:
    missing = {
        node_id
        for item in edges
        for node_id in (item.edge.source_node_id, item.edge.target_node_id)
        if node_id not in nodes_by_id
    }
    if missing:
        raise ValueError("graph edge endpoint is missing from revision facts")


def _node_projection_id(item: GraphNodeProjection) -> str:
    return graph_source_projection_id(
        target_type="node",
        target_id=item.node.node_id,
        resource_id=item.resource_id,
        content_revision=item.content_revision,
        evidence_ids=item.evidence_ids,
        producer_id=item.producer_id,
    )


def _edge_projection_id(item: GraphEdgeProjection) -> str:
    return graph_source_projection_id(
        target_type="edge",
        target_id=item.edge.edge_id,
        resource_id=item.resource_id,
        content_revision=item.content_revision,
        evidence_ids=item.evidence_ids,
        producer_id=item.producer_id,
    )


def _node_index_text(node: GraphNode) -> str:
    return "\n".join(part for part in (node.name, *node.aliases, node.description) if part)


def _edge_index_text(item: GraphEdgeProjection, nodes_by_id: dict[str, GraphNode]) -> str:
    edge = item.edge
    parts = [
        f"{nodes_by_id[edge.source_node_id].name} -> {edge.relation_type} -> {nodes_by_id[edge.target_node_id].name}",
        " ".join(edge.keywords),
        edge.description,
    ]
    return "\n".join(part for part in parts if part)
