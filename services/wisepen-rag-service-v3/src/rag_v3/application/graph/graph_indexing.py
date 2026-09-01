"""将 Mongo 图谱事实独立写入 Neo4j 与两个 Qdrant collection。"""

from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI

from rag_v3.application.document.models import GeneralDocumentMetadata
from rag_v3.application.graph.models import (
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
    graph_source_projection_id,
)
from rag_v3.domain.repositories.acl import ResourceAclRepository
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository
from rag_v3.domain.repositories.documents import DocumentRepository
from rag_v3.domain.repositories.graph_edge_vectors import GraphEdgeVectorRepository
from rag_v3.domain.repositories.graph_fact import GraphFactRepository
from rag_v3.domain.repositories.graph_node_vectors import GraphNodeVectorRepository
from rag_v3.domain.repositories.graph_topology import GraphTopologyRepository
from rag_v3.domain.repositories.index_state import ResourceIndexStateRepository


@dataclass(frozen=True, slots=True)
class GraphIndexResult:
    """一次图谱三路入库的实际产出；关闭全局开关时明确返回 skipped。"""

    resource_id: str
    content_revision: str | None
    node_count: int
    edge_count: int
    skipped: bool = False


class GraphIndexBuilder:
    """将 active revision 的图事实写入 Neo4j、节点向量和关系向量，不参与文档发布。"""

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

    async def index(
        self,
        *,
        resource_id: str,
        content_revision: str | None = None,
    ) -> GraphIndexResult:
        """只处理当前 active revision；旧 revision 绝不写入外部索引。"""
        if not self._enabled:
            return GraphIndexResult(resource_id, None, 0, 0, skipped=True)
        if self._topology is None:
            raise RuntimeError("graph topology repository is not configured")

        target_revision = await self._require_active_revision(
            resource_id=resource_id,
            content_revision=content_revision,
        )
        documents = await self._documents.get_revisions(
            [(resource_id, target_revision)]
        )
        document = documents.get((resource_id, target_revision))
        if document is None:
            raise ValueError("active document is missing")
        # 通用文档没有默认 Ontology；误调用投影也不能创建空的外部图索引。
        if isinstance(document.metadata, GeneralDocumentMetadata):
            return GraphIndexResult(resource_id, target_revision, 0, 0, skipped=True)
        resource_acl = (await self._resource_acls.get_resource_acls([resource_id])).get(
            resource_id
        )
        if resource_acl is None:
            raise PermissionError("resource ACL is missing")
        facts = await self._graph_facts.get_revision_facts(
            resource_id=resource_id,
            content_revision=target_revision,
        )
        nodes_by_id = {item.node.node_id: item.node for item in facts.nodes}

        node_texts = {
            _node_projection_id(item): _node_index_text(item.node)
            for item in facts.nodes
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
        return GraphIndexResult(
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
        if (
            content_revision is not None
            and content_revision != state.applied_content_revision
        ):
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
    return "\n".join(
        part for part in (node.name, *node.aliases, node.description) if part
    )


def _edge_index_text(
    item: GraphEdgeProjection, nodes_by_id: dict[str, GraphNode]
) -> str:
    edge = item.edge
    parts = [
        f"{nodes_by_id[edge.source_node_id].name} -> {edge.relation_type} -> {nodes_by_id[edge.target_node_id].name}",
        " ".join(edge.keywords),
        edge.description,
    ]
    return "\n".join(part for part in parts if part)
