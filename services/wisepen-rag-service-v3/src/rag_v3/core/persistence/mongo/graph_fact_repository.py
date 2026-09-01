"""Beanie adapter：按 revision 保存图谱事实，不实现图查询。"""

from pymongo import ReplaceOne

from rag_v3.domain.entities.documents import StoredSpan
from rag_v3.domain.entities.graph import (
    GraphEdgeProjectionEntity,
    GraphNodeProjectionEntity,
    TextGraphEvidenceEntity,
)
from rag_v3.domain.graph import (
    GraphEdgeProjection,
    GraphNodeProjection,
    TextGraphEvidence,
)
from rag_v3.domain.repositories.graph import GraphFactRepository


class MongoGraphFactRepository(GraphFactRepository):
    """以完整构建结果替换同一 revision 的图谱 Mongo 投影。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: tuple[GraphNodeProjection, ...],
        edges: tuple[GraphEdgeProjection, ...],
        evidences: tuple[TextGraphEvidence, ...],
    ) -> None:
        # 图谱尚未参与 active 发布；构建成功后一次替换，重试不会累积旧的模型输出。
        revision_filter = {
            "resource_id": resource_id,
            "content_revision": content_revision,
        }
        await GraphNodeProjectionEntity.find(revision_filter).delete()
        await GraphEdgeProjectionEntity.find(revision_filter).delete()
        await TextGraphEvidenceEntity.find(revision_filter).delete()
        await _replace_nodes(nodes)
        await _replace_edges(edges)
        await _replace_evidences(evidences)


async def _replace_nodes(nodes: tuple[GraphNodeProjection, ...]) -> None:
    if not nodes:
        return
    await GraphNodeProjectionEntity.get_pymongo_collection().bulk_write(
        [
            ReplaceOne(
                {
                    "resource_id": node.resource_id,
                    "content_revision": node.content_revision,
                    "node.node_id": node.node.node_id,
                    "producer_id": node.producer_id,
                },
                {
                    "node": node.node.model_dump(mode="json"),
                    "resource_id": node.resource_id,
                    "content_revision": node.content_revision,
                    "evidence_ids": list(node.evidence_ids),
                    "producer_id": node.producer_id,
                },
                upsert=True,
            )
            for node in nodes
        ]
    )


async def _replace_edges(edges: tuple[GraphEdgeProjection, ...]) -> None:
    if not edges:
        return
    await GraphEdgeProjectionEntity.get_pymongo_collection().bulk_write(
        [
            ReplaceOne(
                {
                    "resource_id": edge.resource_id,
                    "content_revision": edge.content_revision,
                    "edge.edge_id": edge.edge.edge_id,
                    "producer_id": edge.producer_id,
                },
                {
                    "edge": edge.edge.model_dump(mode="json"),
                    "resource_id": edge.resource_id,
                    "content_revision": edge.content_revision,
                    "evidence_ids": list(edge.evidence_ids),
                    "producer_id": edge.producer_id,
                },
                upsert=True,
            )
            for edge in edges
        ]
    )


async def _replace_evidences(evidences: tuple[TextGraphEvidence, ...]) -> None:
    if not evidences:
        return
    await TextGraphEvidenceEntity.get_pymongo_collection().bulk_write(
        [
            ReplaceOne(
                {"evidence_id": evidence.evidence_id},
                {
                    "evidence_id": evidence.evidence_id,
                    "target_type": evidence.target_type,
                    "target_id": evidence.target_id,
                    "resource_id": evidence.resource_id,
                    "content_revision": evidence.content_revision,
                    "section_id": evidence.section_id,
                    "chunk_id": evidence.chunk_id,
                    "source_spans": [
                        StoredSpan(
                            start_offset=span.start_offset,
                            end_offset=span.end_offset,
                        )
                        for span in evidence.source_spans
                    ],
                    "quote_text": evidence.quote_text,
                },
                upsert=True,
            )
            for evidence in evidences
        ]
    )
