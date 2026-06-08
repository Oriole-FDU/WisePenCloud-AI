from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from beanie import Document
from pydantic import ConfigDict, Field
from pymongo import ASCENDING, IndexModel

from chat.application.rag.enums import ResourceKind
from chat.application.rag.permissions import (
    acl_projection_from_dict,
    acl_projection_to_dict,
)
from chat.application.rag.runtime.models import RagIndexManifest


class RagIndexManifestDocument(Document):

    user_id: str = Field(..., description="用户 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    resource_id: str = Field(..., description="资源 ID")

    resource_version: int = Field(..., description="当前发布索引对应的资源事实版本")
    material_hash: str = Field(..., description="当前发布索引对应的资源材料 hash")
    pipeline_version: str = Field(..., description="当前发布索引对应的 RAG pipeline 版本")
    current_index_version: str = Field(..., description="当前线上检索使用的 index_version")
    acl_projection: Dict[str, Any] = Field(..., description="RAG 本地 ACL 投影")
    owner_id: str = Field(..., description="资源 owner 用户 ID")
    group_ids: List[str] = Field(default_factory=list, description="资源绑定的 group ID")
    admin_group_ids: List[str] = Field(default_factory=list, description="管理员可见 group ID")
    member_view_group_ids: List[str] = Field(
        default_factory=list,
        description="baseMask 包含 VIEW 的普通成员可见 group ID",
    )
    member_view_user_ids: List[str] = Field(
        default_factory=list,
        description="group userMasks 包含 VIEW 的指定成员白名单",
    )
    specified_view_user_ids: List[str] = Field(
        default_factory=list,
        description="资源级指定 VIEW 用户",
    )
    denied_user_ids: List[str] = Field(
        default_factory=list,
        description="group userMasks 明确不含 VIEW 的用户黑名单",
    )
    is_deleted: bool = Field(default=False, description="是否已删除")
    is_trashed: bool = Field(default=False, description="是否在回收站")
    is_physically_destroyed: bool = Field(default=False, description="是否已物理销毁")
    acl_version: Optional[str] = Field(default=None, description="ACL 投影版本")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(frozen=False)

    class Settings:
        name = "rag_index_manifests"
        indexes = [
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("resource_kind", ASCENDING),
                    ("resource_id", ASCENDING),
                ],
                unique=True,
                name="uq_manifest_resource",
            ),
            IndexModel(
                [
                    ("owner_id", ASCENDING),
                    ("resource_kind", ASCENDING),
                    ("is_deleted", ASCENDING),
                    ("is_trashed", ASCENDING),
                    ("is_physically_destroyed", ASCENDING),
                ],
                name="idx_manifest_owner_visible",
            ),
            IndexModel(
                [
                    ("specified_view_user_ids", ASCENDING),
                    ("resource_kind", ASCENDING),
                ],
                name="idx_manifest_specified_view_users",
            ),
            IndexModel(
                [
                    ("admin_group_ids", ASCENDING),
                    ("resource_kind", ASCENDING),
                ],
                name="idx_manifest_admin_groups",
            ),
            IndexModel(
                [
                    ("member_view_group_ids", ASCENDING),
                    ("resource_kind", ASCENDING),
                ],
                name="idx_manifest_member_view_groups",
            ),
            IndexModel(
                [
                    ("member_view_user_ids", ASCENDING),
                    ("resource_kind", ASCENDING),
                ],
                name="idx_manifest_member_view_users",
            ),
        ]

    def to_domain(self) -> RagIndexManifest:
        return RagIndexManifest(
            user_id=self.user_id,
            resource_kind=self.resource_kind,
            resource_id=self.resource_id,
            resource_version=self.resource_version,
            material_hash=self.material_hash,
            pipeline_version=self.pipeline_version,
            current_index_version=self.current_index_version,
            acl_projection=acl_projection_from_dict(self.acl_projection),
        )


def build_manifest_acl_index_fields(manifest: RagIndexManifest) -> Dict[str, Any]:
    """从 ACL 投影生成 Manifest 查询索引字段。"""
    projection = manifest.acl_projection
    group_ids: List[str] = []
    admin_group_ids: List[str] = []
    member_view_group_ids: List[str] = []
    member_view_user_ids: List[str] = []
    specified_view_user_ids: List[str] = []
    denied_user_ids: List[str] = []

    for bind in projection.group_binds:
        if bind.in_trash:
            continue
        group_ids.append(bind.group_id)
        admin_group_ids.append(bind.group_id)

    for group_id, computed_acl in projection.computed_group_acls.items():
        if computed_acl.base_mask & 2 == 2:
            member_view_group_ids.append(group_id)
        for user_id, mask in computed_acl.user_masks.items():
            if mask & 2 == 2:
                member_view_user_ids.append(user_id)
            else:
                denied_user_ids.append(user_id)

    for user_id, mask in projection.specified_users_granted_actions_mask.items():
        if mask & 2 == 2:
            specified_view_user_ids.append(user_id)

    return {
        "acl_projection": acl_projection_to_dict(projection),
        "owner_id": projection.owner_id,
        "group_ids": sorted(set(group_ids)),
        "admin_group_ids": sorted(set(admin_group_ids)),
        "member_view_group_ids": sorted(set(member_view_group_ids)),
        "member_view_user_ids": sorted(set(member_view_user_ids)),
        "specified_view_user_ids": sorted(set(specified_view_user_ids)),
        "denied_user_ids": sorted(set(denied_user_ids)),
        "is_deleted": projection.is_deleted,
        "is_trashed": projection.is_trashed,
        "is_physically_destroyed": projection.is_physically_destroyed,
        "acl_version": projection.acl_version,
    }
