"""RAG ACL 的领域事实、请求身份和资源授权行为。"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from common.core.domain import GroupRoleType


@dataclass(slots=True)
class PermissionScope:
    """一次可信请求中的用户身份和群组角色。"""

    user_id: str
    group_roles: dict[str, GroupRoleType | None] = field(default_factory=dict)

    @property
    def managed_group_ids(self) -> set[str]:
        """返回用户作为 owner/admin 管理的群组。"""
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role in (GroupRoleType.OWNER, GroupRoleType.ADMIN)
        }

    @property
    def joined_group_ids(self) -> set[str]:
        """返回用户实际加入的群组。"""
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role is not None and role is not GroupRoleType.NOT_MEMBER
        }

    @classmethod
    def from_group_roles(
        cls,
        user_id: str,
        group_roles: Mapping[str, GroupRoleType | None],
    ) -> "PermissionScope":
        """将安全上下文提供的角色映射复制为 ACL 规则使用的请求事实。"""
        return cls(user_id=user_id, group_roles=dict(group_roles))


@dataclass(slots=True)
class GroupResourceAcl:
    """一个群组在资源上的默认 VIEW 权限及成员例外。"""

    group_id: str
    default_readable: bool
    readable_users: list[str] = field(default_factory=list)
    excluded_read_users: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResourceAcl:
    """一个资源的完整 VIEW 授权事实。"""

    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: list[str] = field(default_factory=list)
    excluded_read_users: list[str] = field(default_factory=list)
    group_acls: list[GroupResourceAcl] = field(default_factory=list)

    def can_read(self, scope: PermissionScope) -> bool:
        """根据资源和群组规则判断当前请求是否拥有 VIEW 权限。

        资源 owner 和资源级显式授权优先于资源级排除；资源级排除只阻断
        后续普通群组授权。managed group 直接放行，joined group 遵循组默认
        权限和成员例外。
        """
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
