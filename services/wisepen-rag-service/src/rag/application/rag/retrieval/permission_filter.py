from __future__ import annotations

from typing import Any

from qdrant_client import models as qdrant_models

from .models import RagPermissionScope


class RagPermissionFilterBuilder:
    """为各检索后端生成等价的 Resource VIEW 过滤条件。"""

    def build_qdrant_filter(self, scope: RagPermissionScope) -> qdrant_models.Filter:
        # 顶层是 OR 关系：owner、资源级授权、群组授权 任意一个命中即放行。
        should: list[qdrant_models.Condition] = [
            # 资源 owner 直接放行。
            qdrant_models.FieldCondition(key="owner_id", match=qdrant_models.MatchValue(value=scope.user_id)),
            # 资源级显式授权用户。
            qdrant_models.FieldCondition(key="readable_users", match=qdrant_models.MatchValue(value=scope.user_id)),
        ]
        group_filters = self._build_qdrant_group_filters(scope)
        if group_filters:
            # 群组授权分支：必须不在资源级排除名单，AND 至少匹配一个群组授权。
            should.append(
                qdrant_models.Filter(
                    must_not=[
                        # 资源级显式排除优先于群组授权。
                        qdrant_models.FieldCondition(
                            key="excluded_read_users",
                            match=qdrant_models.MatchValue(value=scope.user_id),
                        )
                    ],
                    should=group_filters,
                )
            )
        return qdrant_models.Filter(should=should)

    def build_neo4j_predicate(
            self,
            scope: RagPermissionScope,
            *,
            node_alias: str,
    ) -> tuple[str, dict[str, Any]]:
        if not node_alias.isidentifier():
            raise ValueError("node_alias must be a valid identifier")

        params = {
            "rag_acl_user_id": scope.user_id,
            "rag_acl_managed_group_ids": list(scope.managed_group_ids),
            "rag_acl_joined_group_ids": list(scope.joined_group_ids),
        }
        # 与 Qdrant filter 等价的三层 OR：owner / 资源级授权 / 群组授权。
        # coalesce(..., []) 用于在字段缺失时退化为空列表，避免 IN null 抛错。
        predicate = f"""
        (
          {node_alias}.owner_id = $rag_acl_user_id
          OR $rag_acl_user_id IN coalesce({node_alias}.readable_users, [])
          OR (
            NOT $rag_acl_user_id IN coalesce({node_alias}.excluded_read_users, [])
            AND (
              EXISTS {{
                MATCH ({node_alias})-[:HAS_GROUP_ACL]->(acl:ResourceGroupAcl)
                WHERE acl.group_id IN $rag_acl_managed_group_ids
                  OR (
                    acl.group_id IN $rag_acl_joined_group_ids
                    AND acl.is_readable = true
                    AND NOT $rag_acl_user_id IN coalesce(acl.excluded_read_users, [])
                  )
                  OR (
                    acl.group_id IN $rag_acl_joined_group_ids
                    AND acl.is_readable = false
                    AND $rag_acl_user_id IN coalesce(acl.readable_users, [])
                  )
              }}
            )
          )
        )
        """
        return predicate, params

    def _build_qdrant_group_filters(self, scope: RagPermissionScope) -> list[qdrant_models.Condition]:
        filters: list[qdrant_models.Condition] = []
        if scope.managed_group_ids:
            # 管理员组：用户作为 owner/admin 的群组直接放行，无其他约束。
            filters.append(
                self._nested_qdrant_group_filter(
                    [
                        qdrant_models.FieldCondition(
                            key="group_id",
                            match=qdrant_models.MatchAny(any=list(scope.managed_group_ids)),
                        )
                    ]
                )
            )
        if scope.joined_group_ids:
            joined_ids = list(scope.joined_group_ids)
            filters.extend(
                (
                    # 用户加入的群组默认可读 AND 该组未单独排除此用户。
                    self._nested_qdrant_group_filter(
                        [
                            qdrant_models.FieldCondition(
                                key="group_id", match=qdrant_models.MatchAny(any=joined_ids)
                            ),
                            qdrant_models.FieldCondition(
                                key="is_readable", match=qdrant_models.MatchValue(value=True)
                            ),
                        ],
                        must_not=[
                            qdrant_models.FieldCondition(
                                key="excluded_read_users",
                                match=qdrant_models.MatchValue(value=scope.user_id),
                            )
                        ],
                    ),

                    # 用户加入的群组默认不可读，但被 readable_users 显式列出。
                    self._nested_qdrant_group_filter(
                        [
                            qdrant_models.FieldCondition(
                                key="group_id", match=qdrant_models.MatchAny(any=joined_ids)
                            ),
                            qdrant_models.FieldCondition(
                                key="is_readable", match=qdrant_models.MatchValue(value=False)
                            ),
                            qdrant_models.FieldCondition(
                                key="readable_users", match=qdrant_models.MatchValue(value=scope.user_id)
                            ),
                        ]
                    ),
                )
            )
        return filters

    @staticmethod
    def _nested_qdrant_group_filter(
            filter_clauses: list[qdrant_models.Condition],
            *,
            must_not: list[qdrant_models.Condition] | None = None,
    ) -> qdrant_models.NestedCondition:
        # Qdrant NestedCondition：对数组字段 computed_group_acls 的每个元素应用子 filter，
        # 对应 Neo4j 端通过 HAS_GROUP_ACL 关系遍历的语义。
        return qdrant_models.NestedCondition(
            nested=qdrant_models.Nested(
                key="computed_group_acls",
                filter=qdrant_models.Filter(must=filter_clauses, must_not=must_not),
            )
        )
