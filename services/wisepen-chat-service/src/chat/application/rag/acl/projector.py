from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .models import RagComputedGroupAclProjection, RagResourceAclProjection

# Java 侧 VIEW 权限对应第 1 位。
_VIEW_MASK = 1 << 1


class RagAclProjectionError(ValueError):
    """Resource ACL 权威数据不符合投影契约。"""


class RagAclProjector:
    """将 Java Resource 预计算 ACL 投影为检索使用的 VIEW 权限。"""

    def from_resource_item(self, raw: Mapping[str, Any]) -> RagResourceAclProjection:
        """从 Resource 原始数据构建完整 ACL 投影。"""
        resource_id = raw.get("_id")
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise RagAclProjectionError("_id must be a non-empty string.")

        owner_id = raw.get("ownerId")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise RagAclProjectionError("ownerId must be a non-empty string.")

        update_time = raw.get("updateTime")
        if not isinstance(update_time, datetime):
            raise RagAclProjectionError("updateTime must be a datetime.")
        if update_time.tzinfo is None:
            update_time = update_time.replace(tzinfo=timezone.utc)
        else:
            update_time = update_time.astimezone(timezone.utc)

        # 资源级用户权限：包含 VIEW 表示显式可读，否则为显式排除用户。
        readable_users, excluded_read_users = self._read_resource_users(
            raw.get("specifiedUsersGrantedActionsMask")
        )

        return RagResourceAclProjection(
            resource_id=resource_id.strip(),
            acl_revision=int(update_time.timestamp() * 1000),
            owner_id=owner_id.strip(),
            readable_users=readable_users,
            excluded_read_users=excluded_read_users,
            computed_group_acls=self._read_group_acls(raw.get("computedGroupAcls")),
        )

    def _read_resource_users(self, value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """解析资源级用户权限掩码。"""
        if not isinstance(value, Mapping):
            return (), ()

        readable_users: list[str] = []
        excluded_read_users: list[str] = []

        for user_id, mask in value.items():
            if not isinstance(user_id, str) or not user_id.strip():
                continue
            if isinstance(mask, bool) or not isinstance(mask, int):
                continue

            target = readable_users if self._has_view(mask) else excluded_read_users
            target.append(user_id.strip())

        return tuple(readable_users), tuple(excluded_read_users)

    def _read_group_acls(self, value: Any) -> tuple[RagComputedGroupAclProjection, ...]:
        """解析各用户组的基础权限及组内用户覆盖规则。"""
        if not isinstance(value, Mapping):
            return ()

        projections: list[RagComputedGroupAclProjection] = []

        for group_id, acl in value.items():
            if not isinstance(group_id, str) or not group_id.strip():
                continue
            if not isinstance(acl, Mapping):
                continue

            # baseMask 表示该组对资源的默认权限。
            is_readable = self._has_view(acl.get("baseMask"))
            # userMasks 是在组默认权限之上的用户级覆盖。
            readable_users, excluded_read_users = self._read_group_users(
                acl.get("userMasks"), group_is_readable=is_readable
            )

            projections.append(
                RagComputedGroupAclProjection(
                    group_id=group_id.strip(),
                    is_readable=is_readable,
                    readable_users=readable_users,
                    excluded_read_users=excluded_read_users,
                )
            )

        return tuple(projections)

    def _read_group_users(
            self, value: Any, *, group_is_readable: bool
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """解析组内用户相对于组基础权限的例外规则。"""
        if not isinstance(value, Mapping):
            return (), ()

        readable_users: list[str] = []
        excluded_read_users: list[str] = []

        for user_id, mask in value.items():
            if not isinstance(user_id, str) or not user_id.strip():
                continue
            if isinstance(mask, bool) or not isinstance(mask, int):
                continue

            has_view = self._has_view(mask)

            # 组默认可读时，只记录被显式取消 VIEW 的用户。
            if group_is_readable and not has_view:
                excluded_read_users.append(user_id.strip())
            # 组默认不可读时，只记录被显式授予 VIEW 的用户。
            elif not group_is_readable and has_view:
                readable_users.append(user_id.strip())

        return tuple(readable_users), tuple(excluded_read_users)

    @staticmethod
    def _has_view(mask: Any) -> bool:
        """判断权限掩码中是否包含 VIEW 位。"""
        return (
                isinstance(mask, int)
                and not isinstance(mask, bool)
                and (mask & _VIEW_MASK) != 0
        )
