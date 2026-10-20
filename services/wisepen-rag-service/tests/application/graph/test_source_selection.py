"""来源预选控制送入精排的 Chunk，不改变共享图上下文。"""


async def test_top_k_can_exceed_graph_seed_budget(graph_env):
    env = graph_env
    env.entities[:] = [env.match()]
    result = await env.retrieval.retrieve(
        "query", snapshot=env.snapshot(), mode="local", candidate_limit=1, top_k=10
    )
    assert len(result.entities) == 1
    assert len(result.entities[0].sources) == 2
    assert {chunk.resource_id for chunk in result.chunks} == {"a", "c"}


async def test_local_does_not_promote_neighbor_or_read_its_second_hop(graph_env):
    env = graph_env
    env.entities[:] = [env.match()]
    result = await env.retrieval.retrieve(
        "query", snapshot=env.snapshot(), mode="local"
    )
    assert {node.id for node in result.entities} == {env.facts[0][0][0].id}
    assert {edge.id for edge in result.relationships} == {env.facts[0][1][0].id}
    limited = [
        call
        for call in env.graph.get_neighbors.call_args_list
        if call.kwargs["limit"] is not None
    ]
    assert len(limited) == 1
    assert limited[0].args[0] == [env.facts[0][0][0].id]


async def test_context_budget_does_not_truncate_navigation_connections(graph_env):
    env = graph_env
    env.entities[:] = [env.match(position=1)]
    result = await env.retrieval.retrieve(
        "query", snapshot=env.snapshot(), mode="local", neighbor_limit=1
    )
    assert len({edge.id for edge in result.relationships}) == 1
    company = next(
        seed
        for hit in result.chunks
        for seed in hit.seed_entities
        if seed.id == env.facts[0][0][1].id
    )
    assert {option.relation_type for option in company.relations} == {
        "works_at",
        "supplies",
    }
