"""Redis 中候选图子图缓存的序列化、失效和并发填充实现。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace

from redis.asyncio import Redis

from rag.domain.models.acl import PermissionScope
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeEntityType,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelationType,
    TraversalDirection,
)
from rag.domain.repositories.neo4j.knowledge_graph_repository import (
    GraphQuerySubgraph,
    TraversedEdge,
    TraversedPath,
)
from rag.domain.repositories.redis.graph_query_subgraph_cache import (
    GraphQuerySubgraphCache,
)
from common.utils.document import SourceSpan

_CACHE_PREFIX = "wisepen:rag:v2:graph-query-subgraph:"
_EPOCH_KEY = f"{_CACHE_PREFIX}epoch"
_CACHE_SCHEMA_VERSION = "graph-query-subgraph:v1"


class RedisGraphQuerySubgraphCache(GraphQuerySubgraphCache):
    """缓存 ACL 已过滤但尚未完成证据核验的候选子图。"""

    def __init__(
        self,
        *,
        redis_client: Redis,
        ttl_seconds: int = 300,
        max_paths: int = 80,
        max_bytes: int = 1_048_576,
    ) -> None:
        if ttl_seconds <= 0 or max_paths <= 0 or max_bytes <= 0:
            raise ValueError("graph query cache limits must be positive")
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._max_paths = max_paths
        self._max_bytes = max_bytes

    @property
    def canonical_path_limit(self) -> int:
        return self._max_paths

    async def get_or_load(
        self,
        *,
        seed_node_ids: Sequence[str],
        permission_scope: PermissionScope,
        relation_types: Sequence[KnowledgeRelationType],
        direction: TraversalDirection,
        max_depth: int,
        path_limit: int,
        mention_limit_per_node: int,
        loader: Callable[[], Awaitable[GraphQuerySubgraph]],
    ) -> GraphQuerySubgraph:
        canonical = self._canonical_query(
            seed_node_ids=seed_node_ids,
            permission_scope=permission_scope,
            relation_types=relation_types,
            direction=direction,
            max_depth=max_depth,
            mention_limit_per_node=mention_limit_per_node,
        )
        # 所有请求都填充同一个候选池上限，小 limit 只在调用方截断。
        canonical["path_limit"] = self._max_paths
        epoch = await self._epoch()
        key = self._cache_key(canonical, epoch)

        cached = await self._read(key, canonical, epoch)
        if cached is not None:
            return cached

        loaded = await loader()
        value = replace(
            loaded,
            seed_node_ids=list(canonical["seed_node_ids"]),
            relation_types=[
                KnowledgeRelationType(value)
                for value in canonical["relation_types"]
            ],
            direction=direction,
            max_depth=max_depth,
            path_limit=self._max_paths,
            mention_limit_per_node=mention_limit_per_node,
            graph_epoch=epoch,
            cache_schema_version=_CACHE_SCHEMA_VERSION,
        )
        await self._write(key, value, canonical, epoch)
        return value

    async def bump_epoch(self) -> str:
        return str(await self._redis.incr(_EPOCH_KEY))

    async def _epoch(self) -> str:
        value = await self._redis.get(_EPOCH_KEY)
        if value is None:
            await self._redis.set(_EPOCH_KEY, "0", nx=True)
            value = await self._redis.get(_EPOCH_KEY) or "0"
        if isinstance(value, bytes):
            value = value.decode()
        return value

    async def _read(
        self,
        key: str,
        canonical: dict[str, object],
        epoch: str,
    ) -> GraphQuerySubgraph | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode()
            return _decode(json.loads(raw), canonical=canonical, epoch=epoch)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            await self._redis.delete(key)
            return None

    async def _write(
        self,
        key: str,
        value: GraphQuerySubgraph,
        canonical: dict[str, object],
        epoch: str,
    ) -> None:
        encoded = json.dumps(
            _encode(value, canonical=canonical, epoch=epoch),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > self._max_bytes:
            return
        ttl = min(self._ttl_seconds, 30) if not value.paths else self._ttl_seconds
        await self._redis.set(key, encoded, ex=ttl)

    @staticmethod
    def _cache_key(canonical: dict[str, object], epoch: str) -> str:
        raw = json.dumps(
            {"cache_schema_version": _CACHE_SCHEMA_VERSION, "graph_epoch": epoch, **canonical},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"{_CACHE_PREFIX}{hashlib.sha256(raw).hexdigest()}"

    @staticmethod
    def _canonical_query(
        *,
        seed_node_ids: Sequence[str],
        permission_scope: PermissionScope,
        relation_types: Sequence[KnowledgeRelationType],
        direction: TraversalDirection,
        max_depth: int,
        mention_limit_per_node: int,
    ) -> dict[str, object]:
        roles = sorted(
            [
                group_id,
                role.value if hasattr(role, "value") else role,
            ]
            for group_id, role in permission_scope.group_roles.items()
        )
        return {
            "permission_scope": {
                "user_id": permission_scope.user_id,
                "group_roles": roles,
            },
            "seed_node_ids": sorted(set(seed_node_ids)),
            "relation_types": sorted({item.value for item in relation_types}),
            "direction": direction.value,
            "max_depth": max_depth,
            "mention_limit_per_node": mention_limit_per_node,
        }


def _encode(
    subgraph: GraphQuerySubgraph,
    *,
    canonical: dict[str, object],
    epoch: str,
) -> dict[str, object]:
    return {
        "cache_schema_version": _CACHE_SCHEMA_VERSION,
        "graph_epoch": epoch,
        "query": canonical,
        "paths": [_encode_path(path) for path in subgraph.paths],
        "mentions": [_encode_mention(mention) for mention in subgraph.mentions],
    }


def _encode_path(path: TraversedPath) -> dict[str, object]:
    return {
        "nodes": [
            {
                "node_id": node.node_id,
                "kind": node.kind.value,
                "label": node.label,
                "entity_type": node.entity_type.value if node.entity_type else None,
                "resource_id": node.resource_id,
            }
            for node in path.nodes
        ],
        "edges": [
            {
                "edge_id": edge.edge_id,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "relation_type": edge.relation_type.value,
                "predicate": edge.predicate,
                "evidence": [_encode_evidence(item) for item in edge.evidence],
            }
            for edge in path.edges
        ],
    }


def _encode_evidence(item: GraphEvidence) -> dict[str, object]:
    return {
        "evidence_id": item.evidence_id,
        "resource_id": item.resource_id,
        "content_revision": item.content_revision,
        "reading_block_id": item.reading_block_id,
        "quote": item.quote,
        "start_offset": item.source_span.start_offset,
        "end_offset": item.source_span.end_offset,
    }


def _encode_mention(item: KnowledgeMention) -> dict[str, object]:
    return {
        "mention_id": item.mention_id,
        "node_id": item.node_id,
        "evidence": _encode_evidence(item.evidence),
    }


def _decode(
    payload: dict[str, object],
    *,
    canonical: dict[str, object],
    epoch: str,
) -> GraphQuerySubgraph:
    if payload["cache_schema_version"] != _CACHE_SCHEMA_VERSION:
        raise ValueError("cache schema version mismatch")
    if payload["graph_epoch"] != epoch or payload["query"] != canonical:
        raise ValueError("cache query summary mismatch")
    return GraphQuerySubgraph(
        paths=[_decode_path(item) for item in payload["paths"]],
        mentions=[_decode_mention(item) for item in payload["mentions"]],
        seed_node_ids=list(canonical["seed_node_ids"]),
        relation_types=[KnowledgeRelationType(value) for value in canonical["relation_types"]],
        direction=TraversalDirection(canonical["direction"]),
        max_depth=canonical["max_depth"],
        path_limit=canonical["path_limit"],
        mention_limit_per_node=canonical["mention_limit_per_node"],
        graph_epoch=epoch,
        cache_schema_version=_CACHE_SCHEMA_VERSION,
    )


def _decode_path(item: dict[str, object]) -> TraversedPath:
    return TraversedPath(
        nodes=[_decode_node(value) for value in item["nodes"]],
        edges=[_decode_edge(value) for value in item["edges"]],
    )


def _decode_node(item: dict[str, object]) -> KnowledgeNode:
    kind = KnowledgeNodeKind(item["kind"])
    entity_type = item.get("entity_type")
    return KnowledgeNode(
        node_id=item["node_id"],
        kind=kind,
        label=item["label"],
        entity_type=KnowledgeEntityType(entity_type) if entity_type is not None else None,
        resource_id=item.get("resource_id"),
    )


def _decode_edge(item: dict[str, object]) -> TraversedEdge:
    return TraversedEdge(
        edge_id=item["edge_id"],
        source_node_id=item["source_node_id"],
        target_node_id=item["target_node_id"],
        relation_type=KnowledgeRelationType(item["relation_type"]),
        predicate=item.get("predicate"),
        evidence=[_decode_evidence(value) for value in item["evidence"]],
    )


def _decode_evidence(item: dict[str, object]) -> GraphEvidence:
    return GraphEvidence(
        evidence_id=item["evidence_id"],
        resource_id=item["resource_id"],
        content_revision=item["content_revision"],
        reading_block_id=item["reading_block_id"],
        source_span=SourceSpan(item["start_offset"], item["end_offset"]),
        quote=item["quote"],
    )


def _decode_mention(item: dict[str, object]) -> KnowledgeMention:
    return KnowledgeMention(
        mention_id=item["mention_id"],
        node_id=item["node_id"],
        evidence=_decode_evidence(item["evidence"]),
    )
