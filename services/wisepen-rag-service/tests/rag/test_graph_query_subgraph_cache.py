import asyncio

import pytest

from rag.core.persistence.redis import RedisGraphQuerySubgraphCache
from rag.domain.models.acl import PermissionScope
from rag.domain.models.graph import (
    KnowledgeEntityType,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelationType,
    TraversalDirection,
)
from rag.domain.repositories.neo4j import (
    GraphQuerySubgraph,
    TraversedEdge,
    TraversedPath,
)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)

    async def incr(self, key):
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def eval(self, _script, _keys, key, _token):
        self.values.pop(key, None)
        return 1


def _subgraph() -> GraphQuerySubgraph:
    nodes = [
        KnowledgeNode(
            node_id=node_id,
            kind=KnowledgeNodeKind.ENTITY,
            label=node_id,
            entity_type=KnowledgeEntityType.CONCEPT,
        )
        for node_id in ("node-a", "node-b")
    ]
    return GraphQuerySubgraph(
        paths=[
            TraversedPath(
                nodes=nodes,
                edges=[
                    TraversedEdge(
                        edge_id="edge-1",
                        source_node_id="node-a",
                        target_node_id="node-b",
                        relation_type=KnowledgeRelationType.RELATED_TO,
                        predicate="related",
                    )
                ],
            )
        ]
    )


@pytest.mark.asyncio
async def test_subgraph_cache_canonicalizes_seed_order_and_reuses_loader() -> None:
    redis = _Redis()
    cache = RedisGraphQuerySubgraphCache(redis_client=redis)
    calls = 0

    async def load() -> GraphQuerySubgraph:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return _subgraph()

    common = {
        "permission_scope": PermissionScope(user_id="user-1"),
        "relation_types": [KnowledgeRelationType.RELATED_TO],
        "direction": TraversalDirection.BOTH,
        "max_depth": 1,
        "path_limit": 10,
        "mention_limit_per_node": 3,
        "loader": load,
    }
    await cache.get_or_load(seed_node_ids=["node-b", "node-a"], **common)
    cached = await cache.get_or_load(seed_node_ids=["node-a", "node-b"], **common)

    assert calls == 1
    assert cached.paths[0].edges[0].edge_id == "edge-1"


@pytest.mark.asyncio
async def test_subgraph_cache_epoch_and_corrupt_payload_are_misses() -> None:
    redis = _Redis()
    cache = RedisGraphQuerySubgraphCache(redis_client=redis)
    calls = 0

    async def load() -> GraphQuerySubgraph:
        nonlocal calls
        calls += 1
        return _subgraph()

    kwargs = {
        "seed_node_ids": ["node-a"],
        "permission_scope": PermissionScope(user_id="user-1"),
        "relation_types": [],
        "direction": TraversalDirection.BOTH,
        "max_depth": 1,
        "path_limit": 10,
        "mention_limit_per_node": 3,
        "loader": load,
    }
    await cache.get_or_load(**kwargs)
    cache_key = next(key for key in redis.values if "epoch" not in key)
    redis.values[cache_key] = "{}"
    await cache.get_or_load(**kwargs)
    assert calls == 2

    await cache.bump_epoch()
    await cache.get_or_load(**kwargs)
    assert calls == 3
