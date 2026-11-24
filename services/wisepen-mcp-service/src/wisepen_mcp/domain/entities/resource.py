from enum import Enum
from typing import Any

from pydantic import Field

from wisepen_mcp.domain.entities.base import ApiModel


class ResourceType(str, Enum):
    NOTE = "NOTE"
    DRAWIO = "DRAWIO"
    PDF = "PDF"
    DOC = "DOC"
    DOCX = "DOCX"
    PPT = "PPT"
    PPTX = "PPTX"
    XLS = "XLS"
    XLSX = "XLSX"
    SKILL = "SKILL"
    AGENT = "AGENT"
    SINGLE_HTML_PAGE = "SINGLE_HTML_PAGE"
    UNKNOWN = "UNKNOWN"


class ResourceAction(str, Enum):
    DISCOVER = "DISCOVER"
    VIEW = "VIEW"
    LOAD = "LOAD"
    EDIT = "EDIT"
    INLINE_COMMENT = "INLINE_COMMENT"
    DOWNLOAD_WATERMARK = "DOWNLOAD_WATERMARK"
    DOWNLOAD_ORIGINAL = "DOWNLOAD_ORIGINAL"
    FORK = "FORK"
    COMMENT = "COMMENT"
    MARKET_VIEW = "MARKET_VIEW"


class AccessControlScope(str, Enum):
    ALL = "ALL"
    ONLY_ADMIN = "ONLY_ADMIN"
    WHITELIST = "WHITELIST"
    BLACKLIST = "BLACKLIST"


class FileOrganizationLogic(str, Enum):
    FOLDER = "FOLDER"
    TAG = "TAG"


class CommentSortBy(str, Enum):
    CREATE_TIME = "CREATE_TIME"
    LIKE_COUNT = "LIKE_COUNT"


class TagTreeNode(ApiModel):
    tag_id: str = ""
    parent_id: str | None = None
    tag_name: str = ""
    is_path: bool = False
    tag_desc: str | None = None
    tag_creator: str | None = None
    creator_info: dict[str, Any] | None = None
    granted_actions: list[Any] = Field(default_factory=list)
    children: list["TagTreeNode"] = Field(default_factory=list)


class ResourceItem(ApiModel):
    resource_id: str = ""
    resource_name: str = ""
    resource_type: ResourceType | None = None
    owner_id: str | None = None
    preview: str | None = None
    size: Any = None
    create_time: Any = None
    current_actions: list[Any] = Field(default_factory=list)
    override_granted_actions: list[Any] | None = None
    specified_users_granted_actions: list[Any] | None = None
    tag_binds: list[Any] = Field(default_factory=list)
    my_interaction_record: dict[str, Any] | None = None
    owner_info: dict[str, Any] | None = None


class ResourceBaseInfo(ApiModel):
    resource_id: str = ""
    resource_name: str = ""
    resource_type: ResourceType | None = None
    owner_id: str | None = None
    preview: str | None = None
    size: Any = None
    create_time: Any = None
    owner_info: dict[str, Any] | None = None
    resource_interaction_info: dict[str, Any] | None = None


class SearchHitItem(ApiModel):
    resource_id: str = ""
    resource_type: Any = None
    resource_name: str = ""
    highlight_content: str | None = None
    update_time: Any = None


class TagCreateRequest(ApiModel):
    group_id: str | None = None
    parent_id: str | None = None
    tag_name: str
    tag_desc: str | None = None
    tag_icon: str | None = None
    tag_color: str | None = None
    tag_meta_info: str | None = None
    tagged_resource_acl_grant_scope: AccessControlScope | None = None
    tagged_resource_acl_grant_specified_users: list[str] | None = None
    granted_actions: list[ResourceAction] | None = None
    tag_mount_permission_scope: AccessControlScope | None = None
    tag_mount_specified_users: list[str] | None = None


class TagUpdateRequest(ApiModel):
    group_id: str | None = None
    target_tag_id: str
    tag_name: str | None = None
    tag_desc: str | None = None
    tag_icon: str | None = None
    tag_color: str | None = None
    tag_meta_info: str | None = None
    tagged_resource_acl_grant_scope: AccessControlScope | None = None
    tagged_resource_acl_grant_specified_users: list[str] | None = None
    granted_actions: list[ResourceAction] | None = None
    tag_mount_permission_scope: AccessControlScope | None = None
    tag_mount_specified_users: list[str] | None = None


class TagMoveRequest(ApiModel):
    group_id: str | None = None
    target_tag_ids: list[str]
    new_parent_id: str | None = None


class ResourceRenameRequest(ApiModel):
    resource_id: str
    new_name: str


class SetPersonalResourcesPathTagRequest(ApiModel):
    resource_ids: list[str]
    target_path_tag_id: str


class MountResourcesToGroupRequest(ApiModel):
    resource_ids: list[str]
    group_id: str
    target_tag_id: str


class MoveResourcesInGroupRequest(ApiModel):
    resource_source_tag_map: dict[str, str]
    group_id: str
    target_tag_id: str


class UnmountResourcesFromGroupRequest(ApiModel):
    resource_source_tag_map: dict[str, str]
    group_id: str


class MovePersonalResourcesToTrashRequest(ApiModel):
    resource_ids: list[str]


class ResourcePlacementResult(ApiModel):
    resource_count: int | None = None


class TagDeleteRequest(ApiModel):
    group_id: str | None = None
    target_tag_ids: list[str]


class ResourceUpdateActionPermissionRequest(ApiModel):
    resource_id: str
    override_granted_actions: dict[str, list[ResourceAction] | None] | None = None
    specified_users_granted_actions: dict[str, list[ResourceAction]] | None = None


class GroupResConfigResponse(ApiModel):
    group_id: str = ""
    file_org_logic: Any = None
    default_member_actions: list[Any] | None = None


class GroupResConfigUpdateRequest(ApiModel):
    group_id: str
    file_org_logic: FileOrganizationLogic
    default_member_actions: list[ResourceAction] | None = None


class CommentCreateRequest(ApiModel):
    resource_id: str
    content: str
    image_urls: list[str] = Field(default_factory=list)
    ai_authored: bool | None = None


class CommentReplyCreateRequest(ApiModel):
    resource_id: str
    reply_to: str
    content: str
    image_urls: list[str] = Field(default_factory=list)
    ai_authored: bool | None = None


class CommentDeleteRequest(ApiModel):
    resource_id: str
    comment_id: str


class ResourceCommentItemResponse(ApiModel):
    comment_id: str = ""
    resource_id: str = ""
    reply_to_user_id: str | None = None
    author_id: str | None = None
    content: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    like_count: int | None = None
    reply_count: int | None = None
    comment_type: Any = None
    ai_authored: bool | None = None
    author_info: dict[str, Any] | None = None
    reply_to_user_info: dict[str, Any] | None = None
    create_time: Any = None
    deleted: bool | None = None


class InlineCommentItemCreateRequest(ApiModel):
    resource_id: str
    inline_comment_id: str
    content_version: int | None = None
    content: str
    image_urls: list[str] = Field(default_factory=list)
    mention_user_ids: list[str] = Field(default_factory=list)
    ai_authored: bool | None = None


class InlineCommentItemUpdateRequest(ApiModel):
    resource_id: str
    inline_comment_id: str
    item_id: str
    content_version: int | None = None
    content: str
    image_urls: list[str] = Field(default_factory=list)
    mention_user_ids: list[str] = Field(default_factory=list)


class InlineCommentItemDeleteRequest(ApiModel):
    resource_id: str
    inline_comment_id: str
    item_id: str


class InlineCommentResolveRequest(ApiModel):
    resource_id: str
    inline_comment_id: str
    resolved: bool
    content_version: int | None = None


class ResourceInlineCommentResponse(ApiModel):
    inline_comment_id: str = ""
    resource_id: str = ""
    applicable_from_version: int | None = None
    applicable_to_version: int | None = None
    creator_id: str | None = None
    anchor_ref: dict[str, Any] | None = None
    resolved: bool = False
    resolved_by: str | None = None
    resolved_at: Any = None
    creator_info: dict[str, Any] | None = None
    resolved_by_info: dict[str, Any] | None = None
    create_time: Any = None
    update_time: Any = None
    items: list[Any] = Field(default_factory=list)


class ResourceUserInteractionRecordResponse(ApiModel):
    resource_id: str = ""
    read: bool = False
    liked: bool = False
    score: int | None = None
    liked_comment_ids: list[str] = Field(default_factory=list)


class ResourceFavoriteRequest(ApiModel):
    resource_id: str
    favorite: bool
    collection_ids: list[str] | None = None


class FavoriteCollectionCreateRequest(ApiModel):
    collection_name: str
    description: str | None = None


class FavoriteCollectionInfoUpdateRequest(ApiModel):
    collection_id: str
    collection_name: str
    description: str | None = None


class FavoriteCollectionDeleteRequest(ApiModel):
    collection_id: str
    keep_resources_to_default: bool = False


class ResourceFavoriteStatusResponse(ApiModel):
    collection_ids: list[str] = Field(default_factory=list)


class FavoriteCollectionResponse(ApiModel):
    collection_id: str = ""
    collection_name: str = ""
    description: str | None = None
    is_default: bool | None = None
    item_count: int | None = None
    create_time: Any = None


class FavoriteItemResponse(ApiModel):
    resource_id: str = ""
    resource_info: dict[str, Any] | None = None
    favorited_at: Any = None
    collection_ids: list[str] = Field(default_factory=list)
    accessible: bool | None = None


__all__ = [
    "AccessControlScope",
    "CommentCreateRequest",
    "CommentDeleteRequest",
    "CommentReplyCreateRequest",
    "CommentSortBy",
    "FavoriteCollectionCreateRequest",
    "FavoriteCollectionDeleteRequest",
    "FavoriteCollectionInfoUpdateRequest",
    "FavoriteCollectionResponse",
    "FavoriteItemResponse",
    "FileOrganizationLogic",
    "GroupResConfigResponse",
    "GroupResConfigUpdateRequest",
    "InlineCommentItemCreateRequest",
    "InlineCommentItemDeleteRequest",
    "InlineCommentItemUpdateRequest",
    "InlineCommentResolveRequest",
    "MountResourcesToGroupRequest",
    "MovePersonalResourcesToTrashRequest",
    "MoveResourcesInGroupRequest",
    "ResourceAction",
    "ResourceBaseInfo",
    "ResourceCommentItemResponse",
    "ResourceFavoriteRequest",
    "ResourceFavoriteStatusResponse",
    "ResourceInlineCommentResponse",
    "ResourceItem",
    "ResourcePlacementResult",
    "ResourceRenameRequest",
    "ResourceType",
    "ResourceUpdateActionPermissionRequest",
    "ResourceUserInteractionRecordResponse",
    "SearchHitItem",
    "SetPersonalResourcesPathTagRequest",
    "TagCreateRequest",
    "TagDeleteRequest",
    "TagMoveRequest",
    "TagTreeNode",
    "TagUpdateRequest",
    "UnmountResourcesFromGroupRequest",
]
