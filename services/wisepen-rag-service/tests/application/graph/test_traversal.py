"""逐跳调用的显式方向、谓词与来源授权，不能借隐藏边进入下一跳。"""

from dataclasses import asdict, replace
from unittest.mock import AsyncMock

import pytest

from rag.application.acl.models import ResourceAcl
from rag.application.graph import GraphEntity, GraphRelationship, GraphTraversalResult
from rag.core.schema.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from rag.storage.graph import GraphDirection


@pytest.mark.parametrize(
    "direction,types,expected",
    [
        (GraphDirection.BOTH, None, {"works_at", "supplies"}),
        (GraphDirection.OUTGOING, None, {"supplies"}),
        (GraphDirection.INCOMING, None, {"works_at"}),
        (GraphDirection.BOTH, ["works_at"], {"works_at"}),
        (GraphDirection.BOTH, [], set()),
    ],
)
async def test_one_hop_direction_types_and_original_edge_direction(
    graph_env, direction, types, expected
):
    env = graph_env
    company = env.facts[0][0][1].id
    result = await env.traversal.traverse(
        [company], snapshot=env.snapshot(), direction=direction, relation_types=types
    )
    assert result.entities[0].id == company
    # 以核心事实的完整 ID 核对方向与谓词，不反向拆解应用结果 ID。
    expected_edges = {
        edge.id: edge
        for _, edges in env.facts
        for edge in edges
        if edge.relation_type in expected
    }
    assert {edge.id for edge in result.relationships} == set(expected_edges)
    assert {source.chunk_id for node in result.entities for source in node.sources} == {
        chunk.chunk_id for chunk in env.chunks
    }
    assert len(result.entities) == len({node.id for node in result.entities})
    assert all(
        all(
            (key, source.chunk_id)
            in {
                (node.id, source.chunk_id)
                for node in result.entities
                for source in node.sources
            }
            for key in (
                expected_edges[edge.id].source_id,
                expected_edges[edge.id].target_id,
            )
            for source in edge.sources
        )
        for edge in result.relationships
    )
    if types == []:
        env.graph.get_neighbors.assert_not_awaited()


async def test_shared_topology_groups_sources_and_deduplicates_two_seed_edge(
    graph_env,
):
    env = graph_env
    person, company = [node.id for node in env.facts[0][0]]
    result = await env.traversal.traverse(
        [person, company, person], snapshot=env.snapshot(), relation_types=["works_at"]
    )
    assert set(asdict(result)) == {"entities", "relationships"}
    assert all(isinstance(node, GraphEntity) for node in result.entities)
    assert all(isinstance(edge, GraphRelationship) for edge in result.relationships)
    # 两个种子共享一条逻辑边；保留两份来源，不能按种子重复返回。
    assert len(result.relationships) == 1
    assert len(result.relationships[0].sources) == 2
    assert len(result.entities) == len({node.id for node in result.entities})


async def test_neighbor_descriptions_only_come_from_selected_relationship_sources(
    graph_env,
):
    env = graph_env
    person, company = [node.id for node in env.facts[0][0]]
    result = await env.traversal.traverse([person], snapshot=env.snapshot())
    company_sources = {
        source.chunk_id
        for node in result.entities
        if node.id == company
        for source in node.sources
    }
    assert company_sources == {env.chunks[0].chunk_id, env.chunks[2].chunk_id}
    env.graph.get_nodes.assert_awaited_once_with([person], filters=None)
    assert {edge.id for edge in result.relationships} == {env.facts[0][1][0].id}


async def test_two_explicit_calls_cross_visible_documents_without_automatic_second_hop(
    graph_env,
):
    env = graph_env
    person, company = [node.id for node in env.facts[0][0]]
    snapshot = env.snapshot()
    first = await env.traversal.traverse(
        [person], snapshot=snapshot, direction="outgoing", relation_types=["works_at"]
    )
    assert {edge.id for edge in first.relationships} == {env.facts[0][1][0].id}
    assert {source.chunk_id for node in first.entities for source in node.sources} == {
        env.chunks[0].chunk_id,
        env.chunks[2].chunk_id,
    }
    assert company in {node.id for node in first.entities}
    second = await env.traversal.traverse(
        [company], snapshot=snapshot, direction="outgoing", relation_types=["supplies"]
    )
    assert {edge.id for edge in second.relationships} == {env.facts[1][1][0].id}
    assert env.graph.get_neighbors.await_count == 2


async def test_hidden_bridge_never_supplies_returned_endpoint_or_expandable_seed(
    graph_env, read_acls
):
    env = graph_env
    read_acls["b"] = ResourceAcl("b", 1, "other")
    company, other = [node.id for node in env.facts[1][0]]
    first = await env.traversal.traverse([company], snapshot=env.snapshot())
    assert all(node.id != other for node in first.entities)
    assert all(
        source.chunk_id != env.chunks[1].chunk_id
        for node in first.entities
        for source in node.sources
    )
    env.graph.get_neighbors.reset_mock()
    assert (
        await env.traversal.traverse([other], snapshot=env.snapshot())
        == GraphTraversalResult()
    )
    env.graph.get_neighbors.assert_not_awaited()


async def test_batch_seed_order_dedup_unknown_and_logical_relation_limit(graph_env):
    env = graph_env
    person, company = [node.id for node in env.facts[0][0]]
    result = await env.traversal.traverse(
        [company, person, company, "unknown"], snapshot=env.snapshot(), relation_limit=1
    )
    assert list(dict.fromkeys(node.id for node in result.entities))[:2] == [
        company,
        person,
    ]
    env.graph.get_nodes.assert_awaited_once()
    assert env.graph.get_nodes.call_args.args[0] == [company, person, "unknown"]
    env.graph.get_neighbors.assert_awaited_once()
    assert env.graph.get_neighbors.call_args.kwargs["limit"] == 1
    # 字典序先选中公司供应关系，不会按种子顺序或 degree 重新排序。
    assert len({edge.id for edge in result.relationships}) == 1
    assert len(result.relationships) == 1
    selected = await env.traversal.traverse(
        [person], snapshot=env.snapshot(), relation_types=["works_at"], relation_limit=1
    )
    assert len(selected.relationships) == 1
    assert len(selected.relationships[0].sources) == 2  # 两个来源归属同一逻辑关系。


async def test_self_loop_reverse_and_different_predicates_keep_identity(
    graph_env, storage
):
    env = graph_env
    chunk = env.chunks[1]
    nodes, edges = env.facts[1]
    by_id = {node.id: node for node in nodes}
    reverse = env.ontology.create_edge(
        nodes=by_id,
        source_id=nodes[1].id,
        target_id=nodes[0].id,
        relation_type="supplies",
        chunk_id=chunk.chunk_id,
    )
    loop = env.ontology.create_edge(
        nodes=by_id,
        source_id=nodes[0].id,
        target_id=nodes[0].id,
        relation_type="supplies",
        chunk_id=chunk.chunk_id,
    )
    chunk.metadata["relationships"].extend([asdict(reverse), asdict(loop)])
    await storage.upsert_chunks([chunk])
    env.graph.edges.extend([reverse, loop])
    result = await env.traversal.traverse([nodes[0].id], snapshot=env.snapshot())
    assert {edge.id for edge in result.relationships} == {
        env.facts[0][1][0].id,
        edges[0].id,
        reverse.id,
        loop.id,
    }
    assert len(result.relationships) == 4


async def test_stale_projection_cannot_expand_deleted_root_or_deleted_endpoint(
    graph_env, storage
):
    env = graph_env
    person = env.facts[0][0][0].id
    for index in (0, 2):
        env.chunks[index].metadata["entities"] = env.chunks[index].metadata["entities"][
            1:
        ]
    await storage.upsert_chunks([env.chunks[0], env.chunks[2]])
    assert (
        await env.traversal.traverse([person], snapshot=env.snapshot())
        == GraphTraversalResult()
    )
    env.graph.get_neighbors.assert_not_awaited()
    company = env.facts[0][0][1].id
    result = await env.traversal.traverse([company], snapshot=env.snapshot())
    assert {edge.id for edge in result.relationships} == {env.facts[1][1][0].id}


async def test_empty_ids_no_io_and_depth_is_not_an_available_parameter(
    graph_env, storage
):
    env = graph_env
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    assert (
        await env.traversal.traverse([], snapshot=env.snapshot())
        == GraphTraversalResult()
    )
    with pytest.raises(TypeError):
        await env.traversal.traverse([], snapshot=env.snapshot(), depth=2)
    storage.get_chunks.assert_not_awaited()
    env.graph.get_nodes.assert_not_awaited()


async def test_graph_filters_apply_to_root_and_relationship_sources(graph_env):
    env = graph_env
    filters = MetadataFilters([MetadataFilter("resource_id", "a")])
    result = await env.traversal.traverse(
        [env.facts[0][0][0].id], snapshot=env.snapshot(), filters=filters
    )
    assert {source.chunk_id for node in result.entities for source in node.sources} == {
        env.chunks[0].chunk_id
    }
    assert len(result.relationships) == 1
    assert env.graph.get_nodes.call_args.kwargs["filters"] is filters
    assert env.graph.get_neighbors.call_args.kwargs["filters"] is filters


@pytest.mark.parametrize("operator", [FilterOperator.EQ, FilterOperator.IN])
async def test_ontology_filter_uses_current_facts_and_keeps_source_scope(
    graph_env, storage, operator
):
    env = graph_env
    chunk = env.chunks[0]
    foreign = replace(env.ontology, domain="other")
    node = foreign.create_node(
        name="其他人员", category="Person", source_id=chunk.chunk_id
    )
    chunk.metadata["entities"].append(asdict(node))
    # 业务 metadata 不能代替图事实的本体声明。
    chunk.metadata["ontology_name"] = "other"
    await storage.upsert_chunks([chunk])
    env.graph.nodes.append(node)
    filters = MetadataFilters(
        [
            MetadataFilter(
                "ontology_name",
                env.ontology.domain
                if operator is FilterOperator.EQ
                else [env.ontology.domain],
                operator,
            ),
            MetadataFilters([MetadataFilter("resource_id", "c")], FilterCondition.NOT),
        ]
    )
    result = await env.traversal.traverse(
        [env.facts[0][0][0].id, node.id], snapshot=env.snapshot(), filters=filters
    )
    assert result.entities[0].id == env.facts[0][0][0].id
    assert {source.chunk_id for node in result.entities for source in node.sources} == {
        env.chunks[0].chunk_id
    }
    assert len(result.relationships) == 1
    assert all(item.id != node.id for item in result.entities)
    assert env.graph.get_neighbors.call_args.args[0] == [env.facts[0][0][0].id]

    # OR 不把查询绑定到单一本体；任意一条来源条件匹配即可。
    combined = MetadataFilters(
        [
            MetadataFilter("ontology_name", "other"),
            MetadataFilter("content_revision", env.chunks[2].content_revision),
        ],
        FilterCondition.OR,
    )
    result = await env.traversal.traverse(
        [node.id, env.facts[0][0][0].id], snapshot=env.snapshot(), filters=combined
    )
    assert list(dict.fromkeys(item.id for item in result.entities))[:2] == [
        node.id,
        env.facts[0][0][0].id,
    ]
    assert {
        source.chunk_id
        for item in result.entities
        if item.id == env.facts[0][0][0].id
        for source in item.sources
    } == {env.chunks[2].chunk_id}
    assert {edge.id for edge in result.relationships} == {env.facts[0][1][0].id}


async def test_stale_ontology_projection_cannot_confirm_a_seed(graph_env, storage):
    env = graph_env
    company = env.facts[1][0][1]
    # 保留旧图库投影；当前 Mongo 实体换了本体，其逻辑 ID 已不属于旧种子。
    env.chunks[1].metadata["entities"][1]["ontology_name"] = "other"
    await storage.upsert_chunks([env.chunks[1]])
    result = await env.traversal.traverse(
        [company.id],
        snapshot=env.snapshot(),
        filters=MetadataFilters([MetadataFilter("ontology_name", env.ontology.domain)]),
    )
    assert result == GraphTraversalResult()
    env.graph.get_neighbors.assert_not_awaited()


async def test_invalid_direction_fails_before_io(graph_env):
    # 种子与预算由调用方校验；这里只验证枚举转换仍拒绝未知方向。
    env = graph_env
    with pytest.raises(ValueError):
        await env.traversal.traverse(
            [env.facts[0][0][0].id], snapshot=env.snapshot(), direction="random"
        )
    env.graph.get_nodes.assert_not_awaited()
    env.graph.get_neighbors.assert_not_awaited()


async def test_category_hint_can_be_selected_when_visible_relation_does_not_exist(
    graph_env,
):
    env = graph_env
    other = env.facts[1][0][1]
    assert ("supplies", "both") in [
        (option.relation_type, option.direction) for option in other.relations
    ]
    result = await env.traversal.traverse(
        [other.id],
        snapshot=env.snapshot(),
        direction="outgoing",
        relation_types=["supplies"],
    )
    assert result.relationships == []  # 类别允许，但该实体没有可见的 outgoing 事实。
    assert result.entities[0].id == other.id
    assert set(asdict(result.entities[0])) == {"id", "sources"}
    unknown = await env.traversal.traverse(
        [other.id], snapshot=env.snapshot(), relation_types=["undeclared_here"]
    )
    assert unknown.relationships == []  # 读取不套用写入本体的谓词白名单。
