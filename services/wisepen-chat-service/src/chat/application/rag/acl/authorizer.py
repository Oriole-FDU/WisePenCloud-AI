from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from chat.application.rag.repositories import RagAclProjectionRepository

from .models import RagResourceAclProjection


class RagPermissionIdentity(Protocol):
    """RAG 授权判断所需的最小用户身份信息。"""

    user_id: str

    @property
    def managed_group_ids(self) -> tuple[str, ...]: ...

    @property
    def joined_group_ids(self) -> tuple[str, ...]: ...


class RagPermissionAuthorizer:
    """以本地 Mongo ACL 投影作为返回结果的最终授权门。"""

    __slots__ = ("_repository",)

    def __init__(self, *, repository: RagAclProjectionRepository) -> None:
        self._repository = repository

    async def accessible_resource_ids(
        self, resource_ids: Iterable[str], scope: RagPermissionIdentity
    ) -> frozenset[str]:
        """返回当前用户具有 VIEW 权限的资源 ID。"""
        # 去重并保留原始顺序，减少无意义的批量查询。
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return frozenset()

        projections = await self._repository.get_projections(unique_resource_ids)

        # 用户组集合在整个批次内保持不变，只需构建一次。
        managed_group_ids = frozenset(scope.managed_group_ids)
        joined_group_ids = frozenset(scope.joined_group_ids)

        return frozenset(
            resource_id
            for resource_id, projection in projections.items()
            if _can_view_resource(
                projection,
                user_id=scope.user_id,
                managed_group_ids=managed_group_ids,
                joined_group_ids=joined_group_ids,
            )
        )


def _can_view_resource(
    projection: RagResourceAclProjection,
    *,
    user_id: str,
    managed_group_ids: frozenset[str],
    joined_group_ids: frozenset[str],
) -> bool:
    """根据资源级 ACL 和用户组 ACL 判断用户是否可读。"""
    # 资源所有者和资源级显式授权用户直接放行。
    if user_id == projection.owner_id:
        return True
    if user_id in projection.readable_users:
        return True

    # 资源级显式排除优先于后续普通用户组授权。
    if user_id in projection.excluded_read_users:
        return False

    for acl in projection.computed_group_acls:
        # 用户是该组的管理员或所有者时直接放行。
        if acl.group_id in managed_group_ids:
            return True

        # 用户未加入该组时，该组 ACL 与其无关。
        if acl.group_id not in joined_group_ids:
            continue

        # 组默认可读，但允许对个别成员显式排除。
        if acl.is_readable:
            if user_id not in acl.excluded_read_users:
                return True
            continue

        # 组默认不可读，但允许对个别成员显式授权。
        if user_id in acl.readable_users:
            return True

    return False
