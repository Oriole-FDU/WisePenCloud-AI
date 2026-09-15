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
        """按优先级依次判定：所有者 > 显式白名单 > 黑名单排除 > 群组授权。"""
        # 所有者始终可读
        if scope.user_id == self.owner_id:
            return True
        # 显式白名单用户可读
        if scope.user_id in self.readable_users:
            return True
        # 全局排除用户直接拒绝（即使后面群组授权也不能覆盖）
        if scope.user_id in self.excluded_read_users:
            return False

        # 通过群组授权判定
        for group_acl in self.group_acls:
            # 用户作为群组管理员，可读该群组下的资源
            if group_acl.group_id in scope.managed_group_ids:
                return True
            # 用户未加入该群组，跳过
            if group_acl.group_id not in scope.joined_group_ids:
                continue
            # 公开群组：除非用户被单独排除，否则可读
            if group_acl.default_readable:
                if scope.user_id not in group_acl.excluded_read_users:
                    return True
            # 私有群组：用户必须在群组的可读列表中
            elif scope.user_id in group_acl.readable_users:
                return True
        return False