"""RAG 本地 ACL 事实和 VIEW 授权规则。"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from common.core.domain import GroupRoleType


@dataclass(frozen=True, slots=True)
class GroupResourceAcl:
    """群组默认 VIEW 权限和成员例外。"""

    group_id: str
    default_readable: bool
    readable_users: list[str] = field(default_factory=list)
    excluded_read_users: list[str] = field(default_factory=list)


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
        # 浅拷贝一次，避免调用方后续修改影响授权快照
        return cls(user_id=user_id, group_roles=dict(group_roles))

    @property
    def managed_group_ids(self) -> set[str]:
        """用户拥有管理权限（OWNER/ADMIN）的群组 ID 集合。"""
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role in (GroupRoleType.OWNER, GroupRoleType.ADMIN)
        }

    @property
    def joined_group_ids(self) -> set[str]:
        """用户已加入（非 NOT_MEMBER）的群组 ID 集合。"""
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role is not None and role is not GroupRoleType.NOT_MEMBER
        }


@dataclass(frozen=True, slots=True)
class ResourceAcl:
    """一个资源的 RAG 本地 VIEW 授权事实。"""

    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: list[str] = field(default_factory=list)
    excluded_read_users: list[str] = field(default_factory=list)
    group_acls: list[GroupResourceAcl] = field(default_factory=list)

    def can_read(self, scope: PermissionScope) -> bool:
        """按所有者、白名单、排除项和群组规则顺序判定。"""
        user_id = scope.user_id

        # 所有者与全局白名单直接放行；全局排除项优先于普通群组规则
        if user_id == self.owner_id:
            return True
        if user_id in self.readable_users:
            return True
        if user_id in self.excluded_read_users:
            return False

        # 缓存群组角色派生集合，避免循环内重复计算属性
        managed_group_ids = scope.managed_group_ids
        joined_group_ids = scope.joined_group_ids

        for group_acl in self.group_acls:
            # 管理权限不受群组级排除项限制
            if group_acl.group_id in managed_group_ids:
                return True
            if group_acl.group_id not in joined_group_ids:
                continue
            if group_acl.default_readable:
                # 默认可读时，仅群组级排除项能否决
                if user_id not in group_acl.excluded_read_users:
                    return True
            elif user_id in group_acl.readable_users:
                # 默认不可读时，仅群组级白名单可放行
                return True
        return False