"""Beanie adapter：按 revision 保存图谱事实，不实现图查询。"""

from collections.abc import Sequence

from pymongo import ReplaceOne

from rag_v3.application.graph.models import (
    GraphEdgeProjection,
    GraphNodeProjection,
    GraphRevisionFacts,
    TextGraphEvidence,
)
from rag_v3.domain.entities.graph import (
    GraphEdgeProjectionEntity,
    GraphNodeProjectionEntity,
    TextGraphEvidenceEntity,
)
from rag_v3.domain.repositories.graph_fact import GraphFactRepository


class MongoGraphFactRepository(GraphFactRepository):
    """以完整构建结果替换同一 revision 的图谱 Mongo 投影。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: list[GraphNodeProjection],
        edges: list[GraphEdgeProjection],
        evidences: list[TextGraphEvidence],
    ) -> None:
        # 图谱尚未参与 active 发布；构建成功后一次替换，重试不会累积旧的模型输出。
        revision_filter = {
            "resource_id": resource_id,
            "content_revision": content_revision,
        }
        await GraphNodeProjectionEntity.find(revision_filter).delete()
        await GraphEdgeProjectionEntity.find(revision_filter).delete()
        await TextGraphEvidenceEntity.find(revision_filter).delete()

        if nodes:
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
                            "evidence_ids": node.evidence_ids,
                            "producer_id": node.producer_id,
                            "filter_values": node.filter_values,
                        },
                        upsert=True,
                    )
                    for node in nodes
                ]
            )

        if edges:
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
                            "evidence_ids": edge.evidence_ids,
                            "producer_id": edge.producer_id,
                            "filter_values": edge.filter_values,
                        },
                        upsert=True,
                    )
                    for edge in edges
                ]
            )

        if evidences:
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
                            "source_spans": evidence.source_spans,
                            "quote_text": evidence.quote_text,
                        },
                        upsert=True,
                    )
                    for evidence in evidences
                ]
            )

    async def get_revision_facts(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> GraphRevisionFacts:
        """按 revision 批量读取全部事实；投影层不逐图元回查 Mongo。"""
        revision_filter = {
            "resource_id": resource_id,
            "content_revision": content_revision,
        }
        nodes = await GraphNodeProjectionEntity.find(revision_filter).to_list()
        edges = await GraphEdgeProjectionEntity.find(revision_filter).to_list()
        evidences = await TextGraphEvidenceEntity.find(revision_filter).to_list()
        return GraphRevisionFacts(
            nodes=[
                GraphNodeProjection(
                    node=item.node,
                    resource_id=item.resource_id,
                    content_revision=item.content_revision,
                    evidence_ids=item.evidence_ids,
                    producer_id=item.producer_id,
                    filter_values=item.filter_values,
                )
                for item in nodes
            ],
            edges=[
                GraphEdgeProjection(
                    edge=item.edge,
                    resource_id=item.resource_id,
                    content_revision=item.content_revision,
                    evidence_ids=item.evidence_ids,
                    producer_id=item.producer_id,
                    filter_values=item.filter_values,
                )
                for item in edges
            ],
            evidences=[
                TextGraphEvidence(
                    evidence_id=item.evidence_id,
                    target_type=item.target_type,
                    target_id=item.target_id,
                    resource_id=item.resource_id,
                    content_revision=item.content_revision,
                    section_id=item.section_id,
                    chunk_id=item.chunk_id,
                    source_spans=item.source_spans,
                    quote_text=item.quote_text,
                )
                for item in evidences
            ],
        )

    async def get_evidences(self, evidence_ids: Sequence[str]) -> list[TextGraphEvidence]:
        """按 ID 批量回查 LLM 证据；图检索不逐图元访问 Mongo。"""
        ids = list(dict.fromkeys(evidence_ids))
        if not ids:
            return []
        entities = await TextGraphEvidenceEntity.find(
            {"evidence_id": {"$in": ids}}
        ).to_list()
        return [
            TextGraphEvidence(
                evidence_id=item.evidence_id,
                target_type=item.target_type,
                target_id=item.target_id,
                resource_id=item.resource_id,
                content_revision=item.content_revision,
                section_id=item.section_id,
                chunk_id=item.chunk_id,
                source_spans=item.source_spans,
                quote_text=item.quote_text,
            )
            for item in entities
        ]
