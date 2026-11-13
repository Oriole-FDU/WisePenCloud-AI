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


class TagUpdateRequest(ApiModel):
    group_id: str | None = None
    target_tag_id: str
    tag_name: str


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


__all__ = [
    "MountResourcesToGroupRequest",
    "MovePersonalResourcesToTrashRequest",
    "MoveResourcesInGroupRequest",
    "ResourceBaseInfo",
    "ResourceItem",
    "ResourcePlacementResult",
    "ResourceRenameRequest",
    "ResourceType",
    "SearchHitItem",
    "SetPersonalResourcesPathTagRequest",
    "TagCreateRequest",
    "TagMoveRequest",
    "TagTreeNode",
    "TagUpdateRequest",
    "UnmountResourcesFromGroupRequest",
]
