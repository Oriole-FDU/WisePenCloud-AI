from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional


class RagResourceAction(Enum):
    """Java ResourceAction 的 bitmask 值。"""

    DISCOVER = 1 << 0
    VIEW = 1 << 1
    EDIT = 1 << 2
    DOWNLOAD_WATERMARK = 1 << 3
    DOWNLOAD_ORIGINAL = 1 << 4


class RagGroupRole(StrEnum):
    """RAG 权限计算需要的 Java GroupRoleType。"""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    NOT_MEMBER = "NOT_MEMBER"


ALL_ACTIONS_MASK = (1 << len(RagResourceAction)) - 1
PERSONAL_GROUP_PREFIX = "p_"


@dataclass(frozen=True, slots=True)
class RagGroupBindProjection:
    """资源在一个 group/tag 命名空间下的绑定。"""

    group_id: str
    tag_ids: List[str] = field(default_factory=list)
    primary_tag_id: Optional[str] = None
    primary_tag_is_path: Optional[bool] = None
    in_trash: bool = False


@dataclass(frozen=True, slots=True)
class RagComputedGroupAclProjection:
    """Java ComputedGroupAcl 投影：baseMask + userMasks。"""

    base_mask: int = 0
    user_masks: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RagAclProjection:
    """与 Java resource-service 对齐的 RAG 本地 ACL 投影。"""

    owner_id: str
    group_binds: List[RagGroupBindProjection] = field(default_factory=list)
    computed_group_acls: Dict[str, RagComputedGroupAclProjection] = field(
        default_factory=dict
    )
    specified_users_granted_actions_mask: Dict[str, int] = field(default_factory=dict)
    override_granted_actions_mask: Optional[int] = None
    is_deleted: bool = False
    is_trashed: bool = False
    is_physically_destroyed: bool = False
    acl_version: Optional[str] = None


def action_mask_contains(mask: int, action: RagResourceAction) -> bool:
    """判断 Java ResourceAction bitmask 是否包含指定动作。"""
    return (mask & action.value) == action.value


def can_view(
    *,
    user_id: str,
    group_role_map: Dict[str, RagGroupRole],
    projection: RagAclProjection,
) -> bool:
    """判断用户是否可以读取 RAG 资源正文。

    当前实现按已调研的 Java resource-service 语义计算。
    RAG 暴露正文，因此要求 VIEW，而不是 DISCOVER。
    """

    if (
        projection.is_deleted
        or projection.is_trashed
        or projection.is_physically_destroyed
    ):
        return False

    mask = resolve_actions_mask(
        user_id=user_id,
        group_role_map=group_role_map,
        projection=projection,
    )
    return action_mask_contains(mask, RagResourceAction.VIEW)


def resolve_actions_mask(
    *,
    user_id: str,
    group_role_map: Dict[str, RagGroupRole],
    projection: RagAclProjection,
) -> int:
    """计算某个用户对单个资源投影的动作 mask。"""

    if user_id == projection.owner_id:
        return ALL_ACTIONS_MASK

    specified_mask = projection.specified_users_granted_actions_mask.get(user_id)

    if specified_mask is not None:
        current_mask = specified_mask
    else:
        current_mask = _resolve_group_actions_mask(
            user_id=user_id,
            group_role_map=group_role_map,
            projection=projection,
        )

    # Java getResourceInfo 当前会在非 0 的指定用户/组权限后应用 override。
    # 内部 checkPermission 路径略有差异；团队确认最终权威语义前先显式保留。
    if current_mask != 0 and projection.override_granted_actions_mask is not None:
        current_mask = projection.override_granted_actions_mask

    return current_mask


def _resolve_group_actions_mask(
    *,
    user_id: str,
    group_role_map: Dict[str, RagGroupRole],
    projection: RagAclProjection,
) -> int:
    current_mask = 0

    for group_bind in projection.group_binds:
        if group_bind.group_id.startswith(PERSONAL_GROUP_PREFIX):
            continue

        if group_bind.in_trash:
            continue

        user_role = group_role_map.get(group_bind.group_id)
        if user_role is None or user_role == RagGroupRole.NOT_MEMBER:
            continue

        if user_role in (RagGroupRole.ADMIN, RagGroupRole.OWNER):
            return ALL_ACTIONS_MASK

        computed_acl = projection.computed_group_acls.get(group_bind.group_id)
        if computed_acl is None:
            continue

        current_mask |= computed_acl.user_masks.get(user_id, computed_acl.base_mask)

    return current_mask


def normalize_group_role_map(
    group_role_map: Optional[Dict[str, str]],
) -> Dict[str, RagGroupRole]:
    """归一化 API/事件边界传入的字符串角色。"""

    if group_role_map is None:
        return {}

    normalized: Dict[str, RagGroupRole] = {}
    for group_id, role in group_role_map.items():
        normalized[str(group_id)] = RagGroupRole(role)
    return normalized


def build_owner_acl_projection(owner_id: str) -> RagAclProjection:
    """构造仅 owner 可读的默认 ACL 投影。"""
    return RagAclProjection(owner_id=owner_id)


def acl_projection_to_dict(projection: RagAclProjection) -> Dict[str, Any]:
    """将 ACL 投影序列化为可持久化字典。"""
    return {
        "owner_id": projection.owner_id,
        "group_binds": [
            {
                "group_id": bind.group_id,
                "tag_ids": list(bind.tag_ids),
                "primary_tag_id": bind.primary_tag_id,
                "primary_tag_is_path": bind.primary_tag_is_path,
                "in_trash": bind.in_trash,
            }
            for bind in projection.group_binds
        ],
        "computed_group_acls": {
            group_id: {
                "base_mask": acl.base_mask,
                "user_masks": dict(acl.user_masks),
            }
            for group_id, acl in projection.computed_group_acls.items()
        },
        "specified_users_granted_actions_mask": dict(
            projection.specified_users_granted_actions_mask
        ),
        "override_granted_actions_mask": projection.override_granted_actions_mask,
        "is_deleted": projection.is_deleted,
        "is_trashed": projection.is_trashed,
        "is_physically_destroyed": projection.is_physically_destroyed,
        "acl_version": projection.acl_version,
    }


def acl_projection_from_dict(raw: Dict[str, Any]) -> RagAclProjection:
    """从持久化字典恢复 ACL 投影。"""
    return RagAclProjection(
        owner_id=str(raw["owner_id"]),
        group_binds=[
            RagGroupBindProjection(
                group_id=str(bind["group_id"]),
                tag_ids=[str(tag_id) for tag_id in bind.get("tag_ids", [])],
                primary_tag_id=(
                    str(bind["primary_tag_id"])
                    if bind.get("primary_tag_id") is not None
                    else None
                ),
                primary_tag_is_path=bind.get("primary_tag_is_path"),
                in_trash=bool(bind.get("in_trash", False)),
            )
            for bind in raw.get("group_binds", [])
        ],
        computed_group_acls={
            str(group_id): RagComputedGroupAclProjection(
                base_mask=int(acl.get("base_mask", 0)),
                user_masks={
                    str(user_id): int(mask)
                    for user_id, mask in acl.get("user_masks", {}).items()
                },
            )
            for group_id, acl in raw.get("computed_group_acls", {}).items()
        },
        specified_users_granted_actions_mask={
            str(user_id): int(mask)
            for user_id, mask in raw.get(
                "specified_users_granted_actions_mask", {}
            ).items()
        },
        override_granted_actions_mask=(
            int(raw["override_granted_actions_mask"])
            if raw.get("override_granted_actions_mask") is not None
            else None
        ),
        is_deleted=bool(raw.get("is_deleted", False)),
        is_trashed=bool(raw.get("is_trashed", False)),
        is_physically_destroyed=bool(raw.get("is_physically_destroyed", False)),
        acl_version=(
            str(raw["acl_version"]) if raw.get("acl_version") is not None else None
        ),
    )
