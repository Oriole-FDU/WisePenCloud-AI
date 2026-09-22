"""Beanie adapter：按 revision 保存图谱事实，不实现图查询。"""

from collections.abc import Sequence

from pymongo import ReplaceOne

from rag.application.graph.models import (
    GraphEdgeProjection,
    GraphNodeProjection,
    GraphChunkSource,
)
from rag.domain.entities.graph import (
    GraphEdgeProjectionEntity,
    GraphNodeProjectionEntity,
    GraphChunkSourceEntity,
)
from rag.domain.repositories.graph_fact import (
    GraphFactRepository,
    GraphRevisionFacts,
)


class MongoGraphFactRepository(GraphFactRepository):
    """以完整构建结果替换同一 revision 的图谱 Mongo 投影。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: list[GraphNodeProjection],
        edges: list[GraphEdgeProjection],
        sources: list[GraphChunkSource],
    ) -> None:
        # 图谱尚未参与 active 发布；构建成功后一次替换，重试不会累积旧的模型输出。
        revision_filter = {
            "resource_id": resource_id,
            "content_revision": content_revision,
        }
        await GraphNodeProjectionEntity.find(revision_filter).delete()
        await GraphEdgeProjectionEntity.find(revision_filter).delete()
        await GraphChunkSourceEntity.find(revision_filter).delete()

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
                            "source_ids": node.source_ids,
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
                            "source_ids": edge.source_ids,
                            "producer_id": edge.producer_id,
                            "filter_values": edge.filter_values,
                        },
                        upsert=True,
                    )
                    for edge in edges
                ]
            )

        if sources:
            await GraphChunkSourceEntity.get_pymongo_collection().bulk_write(
                [
                    ReplaceOne(
                        {"source_id": source.source_id},
                        {
                            "source_id": source.source_id,
                            "target_type": source.target_type,
                            "target_id": source.target_id,
                            "resource_id": source.resource_id,
                            "content_revision": source.content_revision,
                            "section_id": source.section_id,
                            "chunk_id": source.chunk_id,
                        },
                        upsert=True,
                    )
                    for source in sources
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
        sources = await GraphChunkSourceEntity.find(revision_filter).to_list()
        return GraphRevisionFacts(
            nodes=[
                GraphNodeProjection(
                    node=item.node,
                    resource_id=item.resource_id,
                    content_revision=item.content_revision,
                    source_ids=item.source_ids,
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
                    source_ids=item.source_ids,
                    producer_id=item.producer_id,
                    filter_values=item.filter_values,
                )
                for item in edges
            ],
            sources=[
                GraphChunkSource(
                    source_id=item.source_id,
                    target_type=item.target_type,
                    target_id=item.target_id,
                    resource_id=item.resource_id,
                    content_revision=item.content_revision,
                    section_id=item.section_id,
                    chunk_id=item.chunk_id,
                )
                for item in sources
            ],
        )

    async def get_sources(self, source_ids: Sequence[str]) -> list[GraphChunkSource]:
        """按 ID 批量回查 Chunk 来源；图检索不逐图元访问 Mongo。"""
        ids = list(dict.fromkeys(source_ids))
        if not ids:
            return []
        entities = await GraphChunkSourceEntity.find(
            {"source_id": {"$in": ids}}
        ).to_list()
        return [
            GraphChunkSource(
                source_id=item.source_id,
                target_type=item.target_type,
                target_id=item.target_id,
                resource_id=item.resource_id,
                content_revision=item.content_revision,
                section_id=item.section_id,
                chunk_id=item.chunk_id,
            )
            for item in entities
        ]

    async def get_node_projections(
        self, refs: Sequence[tuple[str, str, str]]
    ) -> list[GraphNodeProjection]:
        unique_refs = list(dict.fromkeys(refs))
        if not unique_refs:
            return []
        clauses = [
            {
                "resource_id": resource_id,
                "content_revision": revision,
                "node.node_id": node_id,
            }
            for resource_id, revision, node_id in unique_refs
        ]
        entities = await GraphNodeProjectionEntity.find({"$or": clauses}).to_list()
        return [
            GraphNodeProjection(
                node=item.node,
                resource_id=item.resource_id,
                content_revision=item.content_revision,
                source_ids=item.source_ids,
                producer_id=item.producer_id,
                filter_values=item.filter_values,
            )
            for item in entities
        ]
