from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RagComputedGroupAclProjection:
    """单个用户组在某一资源上的 ACL 投影。"""

    group_id: str  # 群组 ID。
    is_readable: bool  # 该群组对资源的默认读权限。
    readable_users: tuple[str, ...] = ()  # 在组默认不可读时被显式授予 VIEW 的成员。
    excluded_read_users: tuple[str, ...] = ()  # 在组默认可读时被显式取消 VIEW 的成员。


@dataclass(frozen=True, slots=True)
class RagResourceAclProjection:
    """检索前置过滤需要的 Resource VIEW 权限。"""

    resource_id: str  # 资源 ID。
    acl_revision: int  # 权限版本号（基于 updateTime），用于缓存和增量同步。
    owner_id: str  # 资源所有者，天然具备 VIEW 权限。
    readable_users: tuple[str, ...] = ()  # 资源级显式可读用户。
    excluded_read_users: tuple[str, ...] = ()  # 资源级显式排除用户。
    computed_group_acls: tuple[RagComputedGroupAclProjection, ...] = ()  # 资源上各用户组 ACL 投影。
