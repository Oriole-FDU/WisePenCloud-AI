"""多个 Qdrant 仓储共用的生命周期、ACL 过滤与条件原子。"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from qdrant_client import models as qdrant_models

from rag_v3.domain.acl import PermissionScope


def match_value(key: str, value: Any) -> qdrant_models.FieldCondition:
    return qdrant_models.FieldCondition(
        key=key, match=qdrant_models.MatchValue(value=value)
    )


def match_any(key: str, values: list[Any]) -> qdrant_models.FieldCondition:
    return qdrant_models.FieldCondition(
        key=key, match=qdrant_models.MatchAny(any=values)
    )


def permission_filter(scope: PermissionScope) -> qdrant_models.Filter:
    """与 ResourceAcl.can_read 同顺序的 Qdrant 预过滤；最终授权仍回到 Mongo。"""
    user_id = scope.user_id
    should: list[qdrant_models.Condition] = [
        match_value("owner_id", user_id),
        match_value("readable_users", user_id),
    ]

    group_filters: list[qdrant_models.Condition] = []
    # 处理用户管理的组 ACL
    if scope.managed_group_ids:
        group_filters.append(
            qdrant_models.NestedCondition(
                nested=qdrant_models.Nested(
                    key="group_acls",
                    filter=qdrant_models.Filter(
                        must=[match_any("group_id", list(scope.managed_group_ids))]
                    ),
                )
            )
        )
    # 处理用户加入的组 ACL
    if scope.joined_group_ids:
        joined = list(scope.joined_group_ids)
        # 公开群组，只有用户被单独拉黑时不可读
        group_filters.append(
            qdrant_models.NestedCondition(
                nested=qdrant_models.Nested(
                    key="group_acls",
                    filter=qdrant_models.Filter(
                        must=[
                            match_any("group_id", joined),
                            match_value("default_readable", True),
                        ],
                        must_not=[match_value("excluded_read_users", user_id)],
                    ),
                )
            )
        )
        # 私有群组，只有用户被指定授权时可读
        group_filters.append(
            qdrant_models.NestedCondition(
                nested=qdrant_models.Nested(
                    key="group_acls",
                    filter=qdrant_models.Filter(
                        must=[
                            match_any("group_id", joined),
                            match_value("default_readable", False),
                            match_value("readable_users", user_id),
                        ]
                    ),
                )
            )
        )

    if group_filters:
        should.append(
            qdrant_models.Filter(
                must_not=[match_value("excluded_read_users", user_id)],
                should=group_filters,
            )
        )

    return qdrant_models.Filter(should=should)


class QdrantVectorRepository:
    """复用 Qdrant 投影的集合生命周期和资源级操作。"""

    _payload_indexes: Sequence[tuple[str, qdrant_models.PayloadSchemaType]] = ()

    def __init__(
        self,
        *,
        client: Any,
        collection_name: str,
        dense_vector_size: int,
        dense_vector_name: str,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._dense_vector_size = dense_vector_size
        self._dense_vector_name = dense_vector_name
        self._collection_lock = asyncio.Lock()
        self._collection_ready = False

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        ids = list(dict.fromkeys(resource_ids))
        if not ids or not await self._client.collection_exists(self._collection_name):
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(must=[match_any("resource_id", ids)])
            ),
            wait=True,
        )

    async def _delete_revision(
        self, *, resource_id: str, content_revision: str
    ) -> None:
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(
                    must=[
                        match_value("resource_id", resource_id),
                        match_value("content_revision", content_revision),
                    ]
                )
            ),
            wait=True,
        )

    async def _ensure_collection(
        self,
        *,
        sparse_vectors_config: Mapping[str, qdrant_models.SparseVectorParams]
        | None = None,
    ) -> None:
        if self._collection_ready:
            return
        async with self._collection_lock:
            if self._collection_ready:
                return
            if not await self._client.collection_exists(self._collection_name):
                collection_args = {
                    "collection_name": self._collection_name,
                    "vectors_config": {
                        self._dense_vector_name: qdrant_models.VectorParams(
                            size=self._dense_vector_size,
                            distance=qdrant_models.Distance.COSINE,
                        )
                    },
                }
                if sparse_vectors_config:
                    collection_args["sparse_vectors_config"] = dict(
                        sparse_vectors_config
                    )
                await self._client.create_collection(**collection_args)
                for field_name, schema in self._payload_indexes:
                    await self._client.create_payload_index(
                        collection_name=self._collection_name,
                        field_name=field_name,
                        field_schema=schema,
                        wait=True,
                    )
            self._collection_ready = True
