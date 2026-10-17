"""运行真实 application 流程，导出未裁剪的检索与一跳拓展结果。

运行：.venv/Scripts/python.exe services/wisepen-rag-service/examples/retrieval_outputs.py
所有数据库、embedding 和 rerank I/O 都使用本文件中的固定替身；不访问服务。
"""

import asyncio
import inspect
import json
from dataclasses import asdict, replace
from operator import attrgetter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from common.utils.markdown.chunking.tokenizer import default_token_counter
from common.utils.retrieval import ModelReranker
from pydantic import TypeAdapter

from rag.application.acl.models import PermissionScope, ResourceAcl
from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.application.graph import (
    GraphEntity,
    GraphEntitySource,
    GraphRelationship,
    GraphRelationshipSource,
    GraphRetrieval,
    GraphRetrievalResult,
    GraphTraversal,
    GraphTraversalResult,
)
from rag.application.retrieval import ChunkHit, GraphNodeReference, VectorRetrieval
from rag.core.schema import (
    Chunk,
    ContentRevision,
    EntitySpec,
    GraphEdge,
    GraphNode,
    GraphRelationOption,
    Ontology,
    RelationSpec,
    parse_graph_facts,
)
from rag.storage.graph import (
    GraphDirection,
    GraphVectorKind,
    graph_fact_sha256,
    graph_vector_record_id,
)
from rag.storage.vector import VectorMatch


async def main() -> None:
    """同一组来源贯穿各案例；完整输出、类型定义和断言一起写入示例目录。"""
    ontology = Ontology(
        "work",
        "任职与企业供应关系",
        entity_specs={
            "Person": EntitySpec("Person", "人员"),
            "Company": EntitySpec("Company", "公司"),
        },
        relation_specs={
            "works_at": RelationSpec(
                "works_at", "人员任职于公司", ["Person"], ["Company"]
            ),
            "supplies": RelationSpec(
                "supplies", "公司向另一公司供货", ["Company"], ["Company"]
            ),
        },
    )
    rows = [
        [
            "employment-a",
            "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
            "张三",
            "Person",
            "软件研发人员",
            "甲公司",
            "软件公司",
            "works_at",
        ],
        [
            "employment-b",
            "# 项目负责人\n张三是甲公司的项目负责人，负责项目交付。\n",
            "张三",
            "Person",
            "项目负责人",
            "甲公司",
            "项目交付单位",
            "works_at",
        ],
        [
            "supply-c",
            "# 供应记录\n甲公司向乙公司供应软件系统。\n",
            "甲公司",
            "Company",
            "软件供应商",
            "乙公司",
            "软件采购方",
            "supplies",
        ],
        [
            "private-d",
            "# 私密任职记录\n张三在丙公司兼任顾问。\n",
            "张三",
            "Person",
            "顾问",
            "丙公司",
            "私密客户",
            "works_at",
        ],
    ]
    counter = default_token_counter()
    chunks = []
    revisions = {}
    acls = {}
    for (
        resource,
        text,
        name,
        category,
        description,
        target,
        target_description,
        predicate,
    ) in rows:
        revision = ContentRevision.create(
            resource_id=resource, document_version=1, text=text
        )
        revisions[resource] = revision
        chunk = Chunk(
            chunk_id=f"{revision.content_revision}:chunk:0",
            resource_id=resource,
            content_revision=revision.content_revision,
            chunk_index=0,
            section_id=f"demo-section-{resource}",
            section_path=[text.splitlines()[0].removeprefix("# ")],
            raw_text=text,
            content_token_count=counter.count(text),
            anchor_labels=[],
            metadata={
                "graph_version": "demo-v1",
                "contextual_context": "固定示例上下文",
                "business": {"record": resource},
            },
        )
        nodes = [
            ontology.create_node(
                name=name,
                category=category,
                source_id=chunk.chunk_id,
                description=description,
            ),
            ontology.create_node(
                name=target,
                category="Company",
                source_id=chunk.chunk_id,
                description=target_description,
            ),
        ]
        edge = ontology.create_edge(
            nodes={node.id: node for node in nodes},
            source_id=nodes[0].id,
            target_id=nodes[1].id,
            relation_type=predicate,
            chunk_id=chunk.chunk_id,
            description=text.splitlines()[1],
            keywords=["任职" if predicate == "works_at" else "供应"],
        )
        chunk.metadata.update(
            entities=[asdict(node) for node in nodes], relationships=[asdict(edge)]
        )
        chunks.append(chunk)
        acls[resource] = ResourceAcl(
            resource, 1, "other" if resource == "private-d" else "demo-reader"
        )
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    facts = [parse_graph_facts(chunk) for chunk in chunks]
    all_nodes = [node for nodes, _ in facts for node in nodes.values()]
    all_edges = [edge for _, edges in facts for edge in edges.values()]

    async def read_chunks(ids):
        return [by_id[key] for key in ids if key in by_id]

    async def states(ids):
        return [
            SimpleNamespace(
                resource_id=key, applied_revision=revisions[key], destroyed=False
            )
            for key in ids
        ]

    async def read_acls(ids):
        return {key: acls[key] for key in ids}

    async def read_nodes(ids, *, filters=None):
        return sorted(
            [node for node in all_nodes if node.id in ids],
            key=lambda node: (ids.index(node.id), node.source_id),
        )

    async def read_relations(ids, *, filters=None):
        return sorted(
            [edge for edge in all_edges if edge.id in ids],
            key=lambda edge: (ids.index(edge.id), edge.source_ids[0]),
        )

    async def neighbors(
        ids, *, direction, relation_types=None, filters=None, limit=100
    ):
        matching = [
            edge
            for edge in all_edges
            if (
                (
                    direction in (GraphDirection.OUTGOING, GraphDirection.BOTH)
                    and edge.source_id in ids
                )
                or (
                    direction in (GraphDirection.INCOMING, GraphDirection.BOTH)
                    and edge.target_id in ids
                )
            )
            and (relation_types is None or edge.relation_type in relation_types)
        ]
        selected = sorted({edge.id for edge in matching})[:limit]
        return sorted(
            [edge for edge in matching if edge.id in selected],
            key=lambda edge: (edge.id, edge.source_ids[0]),
        )

    snapshot_reader = ResourceSnapshotReader(
        store=SimpleNamespace(get_states=states),
        resource_acls=SimpleNamespace(get_resource_acls=read_acls),
        reader=SimpleNamespace(get_chunks=read_chunks),
    )
    graph = SimpleNamespace(
        get_nodes=read_nodes, get_relations=read_relations, get_neighbors=neighbors
    )
    embedding = SimpleNamespace(
        dimensions=2,
        embed=AsyncMock(side_effect=lambda *, texts: [[1.0, 0.0] for _ in texts]),
    )

    def query_vectors(request, *, matches):
        # 固定命中用来观察 application 的真实组织与授权行为，不模拟语义质量。
        return [
            replace(match, embedding=[1.0, 0.0] if request.include_embedding else None)
            for match in matches[: request.top_k]
        ]

    graph_matches = []
    for kind, fact in (
        (GraphVectorKind.ENTITY, next(iter(facts[0][0].values()))),
        (GraphVectorKind.RELATIONSHIP, next(iter(facts[0][1].values()))),
    ):
        graph_matches.append(
            VectorMatch(
                graph_vector_record_id(kind, chunks[0].chunk_id, fact.id),
                0.92,
                {
                    "chunk_id": chunks[0].chunk_id,
                    "resource_id": chunks[0].resource_id,
                    "content_revision": chunks[0].content_revision,
                    "fact_id": fact.id,
                    "fact_sha256": graph_fact_sha256(fact, facts[0][0]),
                    "graph_version": "demo-v1",
                },
            )
        )
    entity_reader = SimpleNamespace(
        query=AsyncMock(
            side_effect=lambda request: query_vectors(
                request, matches=graph_matches[:1]
            )
        )
    )
    relationship_reader = SimpleNamespace(
        query=AsyncMock(
            side_effect=lambda request: query_vectors(
                request, matches=graph_matches[1:]
            )
        )
    )
    ordinary_matches = [
        VectorMatch(
            chunk.chunk_id,
            score,
            {
                "resource_id": chunk.resource_id,
                "content_revision": chunk.content_revision,
            },
        )
        for chunk, score in ((chunks[3], 0.99), (chunks[0], 0.93), (chunks[2], 0.81))
    ]
    vector_reader = SimpleNamespace(
        query=AsyncMock(
            side_effect=lambda request: query_vectors(request, matches=ordinary_matches)
        )
    )
    scores = {
        chunk.raw_text: score
        for chunk, score in zip(chunks, [0.97, 0.91, 0.76, 0.99], strict=True)
    }

    async def rerank(*, query, documents, instruct, top_n):
        return sorted(
            [
                {"index": index, "relevance_score": scores[text]}
                for index, text in enumerate(documents)
            ],
            key=lambda item: item["relevance_score"],
            reverse=True,
        )[:top_n]

    reranker = ModelReranker(
        client=SimpleNamespace(model="deterministic-demo-reranker", rerank=rerank),
        text_getter=attrgetter("chunk.raw_text"),
    )
    retrieval = GraphRetrieval(
        snapshot_reader=snapshot_reader,
        graph_reader=graph,
        embedding_client=embedding,
        entity_reader=entity_reader,
        relationship_reader=relationship_reader,
        reranker=reranker,
    )
    ordinary = VectorRetrieval(
        snapshot_reader=snapshot_reader,
        vector_reader=vector_reader,
        embedding_client=embedding,
        reranker=reranker,
        graph_reader=graph,
    )
    traversal = GraphTraversal(snapshot_reader=snapshot_reader, graph_reader=graph)
    snapshot = ResourceSnapshot(PermissionScope("demo-reader"))
    query = "张三在甲公司的任职情况是什么？"
    person = all_nodes[0].id
    company = all_nodes[1].id
    cases = []
    for mode in ("local", "global", "hybrid"):
        result = await retrieval.retrieve(query, snapshot=snapshot, mode=mode, top_k=10)
        cases.append(
            (
                f"graph_{mode}",
                {"query": query, "mode": mode, "top_k": 10},
                result,
                GraphRetrievalResult,
            )
        )
    for mode in ("dense", "sparse", "hybrid"):
        result = await ordinary.retrieve(query, snapshot=snapshot, mode=mode, top_k=10)
        cases.append(
            (
                f"vector_{mode}",
                {"query": query, "mode": mode, "top_k": 10},
                result,
                list[ChunkHit],
            )
        )
    without_graph = VectorRetrieval(
        snapshot_reader=snapshot_reader,
        vector_reader=vector_reader,
        embedding_client=embedding,
        reranker=reranker,
    )
    result = await without_graph.retrieve(
        query, snapshot=snapshot, mode="dense", top_k=10
    )
    assert all(seed.relations is None for hit in result for seed in hit.seed_entities)
    cases.append(
        (
            "vector_graph_disabled",
            {"query": query, "mode": "dense", "top_k": 10, "graph_enabled": False},
            result,
            list[ChunkHit],
        )
    )
    for name, ids, direction in (
        ("traversal_person", [person], GraphDirection.OUTGOING),
        ("traversal_company", [company], GraphDirection.BOTH),
        ("traversal_shared", [person, company], GraphDirection.BOTH),
    ):
        result = await traversal.traverse(ids, snapshot=snapshot, direction=direction)
        cases.append(
            (
                name,
                {"node_ids": ids, "direction": direction.value, "relation_limit": 100},
                result,
                GraphTraversalResult,
            )
        )

    output = Path(__file__).with_suffix("")
    output.mkdir(exist_ok=True)
    inputs = {
        "user_id": "demo-reader",
        "ontology": asdict(ontology),
        "chunks": [asdict(chunk) for chunk in chunks],
        "acls": [asdict(acl) for acl in acls.values()],
        # id 是核心模型的计算属性，不会由 dataclass 序列化自动加入结果。
        "computed_entity_ids": [
            {"id": node.id, "name": node.name, "source_id": node.source_id}
            for node in all_nodes
        ],
    }
    (output / "inputs.json").write_text(
        json.dumps(inputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = [
        "# 检索与一跳拓展完整输出",
        "",
        "本文件由 retrieval_outputs.py 实际运行生成。应用、快照授权与事实回查使用当前代码；存储、embedding、rerank 使用固定本地替身。分数是演示设定，不代表模型质量。",
        "",
        "所有实际序列化字段、默认值、null、metadata、正文、描述和来源 ID 均完整保留。top_k=10 高于案例实际来源数，无任何文本裁剪；完整输入另见 inputs.json。",
        "",
        "ChunkHit.metadata 在响应组装时排除 entities、relationships；输入文件保留完整内部 metadata，图检索的顶层 entities/relationships 和 seed_entities 不受影响。",
        "",
        "向量 dense/sparse/hybrid 的候选替身刻意返回同一组记录，用于对照输出结构；不能据此推断真实检索模式的召回结果相同。vector_graph_disabled 演示 relations 为 null；其他向量案例启用全图连接查询。",
        "",
        "图用例按逻辑 ID 收拢：实体只返回 id/sources，关系返回 id/起点/终点/谓词/sources，各来源描述独立保留。核心 GraphNode.id、GraphEdge.id 是计算属性，仅输入事实的序列化不包含它们；输入文件 computed_entity_ids 供核对。不要将核心 GraphNode.source_id（Chunk）与关系结果 source_id（起点实体）混淆。",
        "",
        "## 固定数据",
        "",
        "employment-a、employment-b 都记录张三任职于甲公司；supply-c 记录甲公司供应乙公司；private-d 属于其他用户，进入候选和图投影后由真实快照授权过滤。",
    ]
    for name, request, result, result_type in cases:
        adapter = TypeAdapter(result_type)
        data = adapter.dump_python(result, mode="json")
        restored = adapter.validate_python(data)
        assert adapter.dump_python(restored, mode="json") == data
        encoded = json.dumps(data, ensure_ascii=False, indent=2)
        (output / f"{name}.json").write_text(encoded + "\n", encoding="utf-8")
        assert "private-d" not in encoded
        hits = result if isinstance(result, list) else getattr(result, "chunks", [])
        assert all(hit.text == by_id[hit.chunk_id].raw_text for hit in hits)
        assert all(
            hit.metadata
            == {
                key: value
                for key, value in by_id[hit.chunk_id].metadata.items()
                if key not in {"entities", "relationships"}
            }
            for hit in hits
        )
        report.extend(
            [
                "",
                f"## {name}",
                "",
                "输入：",
                "",
                "```json",
                json.dumps(request, ensure_ascii=False, indent=2),
                "```",
                "",
                "完整输出：",
                "",
                "```json",
                encoded,
                "```",
            ]
        )
        print(f"{name}: {output / (name + '.json')}")
    for name, result_type in (
        ("graph", GraphRetrievalResult),
        ("vector", list[ChunkHit]),
        ("traversal", GraphTraversalResult),
    ):
        schema = TypeAdapter(result_type).json_schema()
        (output / f"schema_{name}.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (output / "FULL_OUTPUTS.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    models = [
        "# 当前完整数据模型",
        "",
        "以下定义直接从当前 Python 类读取。结果序列化不排除默认值、null 或 metadata。",
        "",
    ]
    for model in (
        GraphDirection,
        GraphRelationOption,
        GraphNode,
        GraphEdge,
        GraphEntitySource,
        GraphEntity,
        GraphRelationshipSource,
        GraphRelationship,
        Chunk,
        GraphNodeReference,
        ChunkHit,
        GraphRetrievalResult,
        GraphTraversalResult,
    ):
        models.extend(
            [
                f"## {model.__name__}",
                "",
                "```python",
                inspect.getsource(model).rstrip(),
                "```",
                "",
            ]
        )
    (output / "MODELS.md").write_text("\n".join(models), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
