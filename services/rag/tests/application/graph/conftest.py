"""使用真实资源快照与 BSON 往返内容存储；图库仅替代拓扑 I/O。"""

from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.application.acl.models import PermissionScope, ResourceAcl
from rag.application.graph import GraphRetrieval, GraphTraversal
from rag.application.snapshot import ResourceSnapshot
from rag.core.schema import RelationSpec
from rag.storage.graph import (
    GraphDirection,
    GraphVectorKind,
    entity_vector_text,
    graph_fact_sha256,
    graph_vector_record_id,
    relationship_vector_text,
)
from rag.storage.vector import VectorMatch


class MemoryGraph:
    """按图库排序与逻辑关系上限返回投影；来源过滤故意留给权威回查验证。"""

    def __init__(self, facts):
        self.nodes = deepcopy([node for nodes, _ in facts for node in nodes])
        self.edges = deepcopy([edge for _, edges in facts for edge in edges])
        self.get_nodes = AsyncMock(side_effect=self._nodes)
        self.get_relations = AsyncMock(side_effect=self._relations)
        self.get_neighbors = AsyncMock(side_effect=self._neighbors)

    async def _nodes(self, ids, **kwargs):
        return sorted(
            (node for node in self.nodes if node.id in ids),
            key=lambda node: (ids.index(node.id), node.source_id),
        )

    async def _relations(self, ids, **kwargs):
        return sorted(
            (edge for edge in self.edges if edge.id in ids),
            key=lambda edge: (ids.index(edge.id), edge.source_ids),
        )

    async def _neighbors(self, ids, *, direction, relation_types, limit, **kwargs):
        edges = [
            edge
            for edge in self.edges
            if (
                direction in (GraphDirection.OUTGOING, GraphDirection.BOTH)
                and edge.source_id in ids
                or direction in (GraphDirection.INCOMING, GraphDirection.BOTH)
                and edge.target_id in ids
            )
            and (relation_types is None or edge.relation_type in relation_types)
        ]
        selected = sorted({edge.id for edge in edges})[:limit]
        return sorted(
            (edge for edge in edges if edge.id in selected),
            key=lambda edge: (edge.id, edge.source_ids),
        )


@pytest.fixture
async def graph_env(
    graph_ontology,
    make_read_document,
    make_read_chunk,
    publish_read_document,
    storage,
    read_acls,
    snapshot_reader,
):
    ontology = replace(
        graph_ontology,
        relation_specs={
            **graph_ontology.relation_specs,
            "supplies": RelationSpec(
                "supplies", "公司供应公司", ["Company"], ["Company"]
            ),
        },
    )
    chunks, facts = [], []
    for key in ("a", "b", "c", "d"):
        document = await publish_read_document(make_read_document(key))
        chunk = replace(
            make_read_chunk(document),
            section_id=f"section-{key}",
            section_path=["章节", key],
            raw_text=f"{key} 完整原文\n" + "材料" * 1000,
            metadata={"contextual_context": "内部上下文", "other": {"preserved": True}},
        )
        if key == "b":
            nodes = [
                ontology.create_node(
                    name=name,
                    category="Company",
                    source_id=chunk.chunk_id,
                    description=f"{key}-{name}",
                )
                for name in ("甲公司", "乙公司")
            ]
            edge = ontology.create_edge(
                nodes={node.id: node for node in nodes},
                source_id=nodes[0].id,
                target_id=nodes[1].id,
                relation_type="supplies",
                chunk_id=chunk.chunk_id,
                description="b-供应",
                keywords=["供应"],
            )
            edges = [edge]
        elif key == "d":
            nodes = [
                ontology.create_node(
                    name="甲公司",
                    category="Company",
                    source_id=chunk.chunk_id,
                    description="d-无关公司描述",
                )
            ]
            edges = []
        else:
            nodes = [
                ontology.create_node(
                    name=name,
                    category=category,
                    source_id=chunk.chunk_id,
                    description=f"{key}-{name}",
                )
                for name, category in (("张三", "Person"), ("甲公司", "Company"))
            ]
            edges = [
                ontology.create_edge(
                    nodes={node.id: node for node in nodes},
                    source_id=nodes[0].id,
                    target_id=nodes[1].id,
                    relation_type="works_at",
                    chunk_id=chunk.chunk_id,
                    description=f"{key}-任职",
                    keywords=[key],
                )
            ]
        chunk.metadata.update(
            entities=[asdict(node) for node in nodes],
            relationships=[asdict(edge) for edge in edges],
        )
        chunks.append(chunk)
        facts.append((nodes, edges))
        read_acls[key] = ResourceAcl(key, 1, "reader")
    await storage.upsert_chunks(chunks)
    graph = MemoryGraph(facts)
    entity_matches, relation_matches = [], []

    def vector_reader(matches):
        async def query(request):
            return [
                replace(
                    match,
                    embedding=match.embedding if request.include_embedding else None,
                )
                for match in matches[: request.top_k]
            ]

        return SimpleNamespace(query=AsyncMock(side_effect=query))

    entity_reader = vector_reader(entity_matches)
    relation_reader = vector_reader(relation_matches)
    embedding = SimpleNamespace(
        dimensions=2, embed=AsyncMock(return_value=[[1.0, 0.0]])
    )
    dependencies = {
        "snapshot_reader": snapshot_reader,
        "graph_reader": graph,
        "embedding_client": embedding,
        "entity_reader": entity_reader,
        "relationship_reader": relation_reader,
    }

    def match(
        index=0,
        kind=GraphVectorKind.ENTITY,
        position=0,
        score=1.0,
        embedding=None,
        **metadata,
    ):
        chunk = chunks[index]
        nodes, edges = facts[index]
        fact = (nodes if kind is GraphVectorKind.ENTITY else edges)[position]
        text = (
            entity_vector_text(fact, ontology)
            if kind is GraphVectorKind.ENTITY
            else relationship_vector_text(
                fact, {node.id: node for node in nodes}, ontology
            )
        )
        return VectorMatch(
            graph_vector_record_id(kind, chunk.chunk_id, fact.id),
            score,
            {
                "chunk_id": chunk.chunk_id,
                "resource_id": chunk.resource_id,
                "content_revision": chunk.content_revision,
                "ontology_name": ontology.domain,
                "fact_id": fact.id,
                "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
                "fact_sha256": graph_fact_sha256(
                    fact, {node.id: node for node in nodes}
                ),
                "graph_version": chunk.metadata.get("graph_version"),
                **metadata,
            },
            embedding if embedding is not None else [1.0, 0.0],
        )

    return SimpleNamespace(
        chunks=chunks,
        facts=facts,
        ontology=ontology,
        graph=graph,
        entities=entity_matches,
        relations=relation_matches,
        entity_reader=entity_reader,
        relation_reader=relation_reader,
        embedding=embedding,
        dependencies=dependencies,
        retrieval=GraphRetrieval(**dependencies),
        match=match,
        traversal=GraphTraversal(snapshot_reader=snapshot_reader, graph_reader=graph),
        snapshot=lambda user="reader": ResourceSnapshot(PermissionScope(user)),
    )
