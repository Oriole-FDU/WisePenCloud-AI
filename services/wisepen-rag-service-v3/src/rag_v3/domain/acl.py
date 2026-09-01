"""RAG 本地 ACL 事实和 VIEW 授权规则。"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from common.core.domain import GroupRoleType


@dataclass(frozen=True, slots=True)
class PermissionScope:
    """一次可信请求中可用于资源授权的用户和群组角色。"""

    user_id: str
    group_roles: dict[str, GroupRoleType | None] = field(default_factory=dict)

    @classmethod
    def from_group_roles(
        cls,
        user_id: str,
        group_roles: Mapping[str, GroupRoleType | None],
    ) -> "PermissionScope":
        return cls(user_id=user_id, group_roles=dict(group_roles))

    @property
    def managed_group_ids(self) -> set[str]:
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role in (GroupRoleType.OWNER, GroupRoleType.ADMIN)
        }

    @property
    def joined_group_ids(self) -> set[str]:
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role is not None and role is not GroupRoleType.NOT_MEMBER
        }


@dataclass(frozen=True, slots=True)
class GroupResourceAcl:
    """群组默认 VIEW 权限和成员例外。"""

    group_id: str
    default_readable: bool
    readable_users: tuple[str, ...] = ()
    excluded_read_users: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceAcl:
    """一个资源的 RAG 本地 VIEW 授权事实。"""

    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: tuple[str, ...] = ()
    excluded_read_users: tuple[str, ...] = ()
    group_acls: tuple[GroupResourceAcl, ...] = ()

    def can_read(self, scope: PermissionScope) -> bool:
        """按旧 RAG 已验证的优先级判断当前请求能否读取资源。"""
        if scope.user_id == self.owner_id:
            return True
        if scope.user_id in self.readable_users:
            return True
        if scope.user_id in self.excluded_read_users:
            return False

        for group_acl in self.group_acls:
            if group_acl.group_id in scope.managed_group_ids:
                return True
            if group_acl.group_id not in scope.joined_group_ids:
                continue
            if group_acl.default_readable:
                if scope.user_id not in group_acl.excluded_read_users:
                    return True
            elif scope.user_id in group_acl.readable_users:
                return True
        return False
