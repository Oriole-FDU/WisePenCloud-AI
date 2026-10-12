"""逐跳调用的显式方向、谓词与来源授权，不能借隐藏边进入下一跳。"""

from dataclasses import asdict, replace
from unittest.mock import AsyncMock

import pytest

from rag.application.acl.models import ResourceAcl
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
        (None, None, {"works_at", "supplies"}),
        (GraphDirection.BOTH, None, {"works_at", "supplies"}),
        (GraphDirection.OUTGOING, None, {"supplies"}),
        (GraphDirection.INCOMING, None, {"works_at"}),
        (None, ["works_at"], {"works_at"}),
        (None, [], set()),
    ],
)
async def test_one_hop_direction_types_and_original_edge_direction(
    graph_env, direction, types, expected
):
    env = graph_env
    company = env.facts[0][0][1].id
    groups = await env.traversal.traverse(
        [company], snapshot=env.snapshot(), direction=direction, relation_types=types
    )
    assert len(groups) == 1 and groups[0].fact_id == company
    assert {edge.relation_type for edge in groups[0].relationships} == expected
    assert all(
        edge.target_id == company
        for edge in groups[0].relationships
        if edge.relation_type == "works_at"
    )
    assert {source.resource_id for source in groups[0].sources} == {"a", "b", "c", "d"}
    assert len(groups[0].entities) == len(
        {(node.id, node.source_id) for node in groups[0].entities}
    )
    assert all(
        edge.source_ids[0] in {source.chunk_id for source in groups[0].sources}
        for edge in groups[0].relationships
    )
    if types == []:
        env.graph.get_neighbors.assert_not_awaited()


async def test_two_explicit_calls_cross_visible_documents_without_automatic_second_hop(
    graph_env,
):
    env = graph_env
    person, company = [node.id for node in env.facts[0][0]]
    snapshot = env.snapshot()
    first = await env.traversal.traverse(
        [person], snapshot=snapshot, direction="outgoing", relation_types=["works_at"]
    )
    assert {edge.relation_type for edge in first[0].relationships} == {"works_at"}
    assert {source.resource_id for source in first[0].sources} == {"a", "c"}
    assert company in {node.id for node in first[0].entities}
    second = await env.traversal.traverse(
        [company], snapshot=snapshot, direction="outgoing", relation_types=["supplies"]
    )
    assert {edge.target_id for edge in second[0].relationships} == {
        env.facts[1][0][1].id
    }
    assert env.graph.get_neighbors.await_count == 2


async def test_hidden_bridge_never_supplies_returned_endpoint_or_expandable_seed(
    graph_env, read_acls
):
    env = graph_env
    read_acls["b"] = ResourceAcl("b", 1, "other")
    company, other = [node.id for node in env.facts[1][0]]
    first = await env.traversal.traverse([company], snapshot=env.snapshot())
    assert all(node.id != other for node in first[0].entities)
    assert all(source.resource_id != "b" for source in first[0].sources)
    env.graph.get_neighbors.reset_mock()
    assert await env.traversal.traverse([other], snapshot=env.snapshot()) == []
    env.graph.get_neighbors.assert_not_awaited()


async def test_batch_seed_order_dedup_unknown_and_logical_relation_limit(graph_env):
    env = graph_env
    person, company = [node.id for node in env.facts[0][0]]
    groups = await env.traversal.traverse(
        [company, person, company, "unknown"], snapshot=env.snapshot(), relation_limit=1
    )
    assert [group.fact_id for group in groups] == [company, person]
    env.graph.get_nodes.assert_awaited_once()
    assert env.graph.get_nodes.call_args.args[0] == [company, person, "unknown"]
    env.graph.get_neighbors.assert_awaited_once()
    assert env.graph.get_neighbors.call_args.kwargs["limit"] == 1
    # 字典序先选中公司供应关系，不会按种子顺序或 degree 重新排序。
    assert groups[1].relationships == []
    assert len({edge.id for group in groups for edge in group.relationships}) == 1
    selected = await env.traversal.traverse(
        [person], snapshot=env.snapshot(), relation_types=["works_at"], relation_limit=1
    )
    assert len(selected[0].relationships) == 2  # 一个逻辑关系的两个来源完整返回。


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
    groups = await env.traversal.traverse([nodes[0].id], snapshot=env.snapshot())
    selected = [
        edge for edge in groups[0].relationships if edge.relation_type == "supplies"
    ]
    assert {edge.id for edge in selected} == {edges[0].id, reverse.id, loop.id}
    assert len(selected) == 3


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
    assert await env.traversal.traverse([person], snapshot=env.snapshot()) == []
    env.graph.get_neighbors.assert_not_awaited()
    company = env.facts[0][0][1].id
    groups = await env.traversal.traverse([company], snapshot=env.snapshot())
    assert {edge.relation_type for edge in groups[0].relationships} == {"supplies"}


async def test_empty_ids_no_io_and_depth_is_not_an_available_parameter(
    graph_env, storage
):
    env = graph_env
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    assert await env.traversal.traverse([], snapshot=env.snapshot()) == []
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
    assert {source.resource_id for source in result[0].sources} == {"a"}
    assert len(result[0].relationships) == 1
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
    groups = await env.traversal.traverse(
        [env.facts[0][0][0].id, node.id], snapshot=env.snapshot(), filters=filters
    )
    assert [group.fact_id for group in groups] == [env.facts[0][0][0].id]
    assert {source.resource_id for source in groups[0].sources} == {"a"}
    assert len(groups[0].relationships) == 1
    assert all(item.id != node.id for item in groups[0].entities)
    assert env.graph.get_neighbors.call_args.args[0] == [groups[0].fact_id]

    # OR 不把查询绑定到单一本体；任意一条来源条件匹配即可。
    combined = MetadataFilters(
        [
            MetadataFilter("ontology_name", "other"),
            MetadataFilter("content_revision", env.chunks[2].content_revision),
        ],
        FilterCondition.OR,
    )
    groups = await env.traversal.traverse(
        [node.id, env.facts[0][0][0].id], snapshot=env.snapshot(), filters=combined
    )
    assert [group.fact_id for group in groups] == [node.id, env.facts[0][0][0].id]
    assert {source.resource_id for source in groups[1].sources} == {"c"}
    assert groups[0].relationships == []


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
    assert result == []
    env.graph.get_neighbors.assert_not_awaited()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"node_ids": [""]},
        {"relation_limit": 0},
        {"direction": "random"},
    ],
)
async def test_invalid_traversal_fails_before_io(graph_env, kwargs):
    env = graph_env
    with pytest.raises(ValueError):
        await env.traversal.traverse(
            snapshot=env.snapshot(), **{"node_ids": [env.facts[0][0][0].id], **kwargs}
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
    groups = await env.traversal.traverse(
        [other.id],
        snapshot=env.snapshot(),
        direction="outgoing",
        relation_types=["supplies"],
    )
    assert groups[0].relationships == []  # 类别允许，但该实体没有可见的 outgoing 事实。
    assert groups[0].entities[0].relations == other.relations
    unknown = await env.traversal.traverse(
        [other.id], snapshot=env.snapshot(), relation_types=["undeclared_here"]
    )
    assert unknown[0].relationships == []  # 读取不套用写入本体的谓词白名单。
