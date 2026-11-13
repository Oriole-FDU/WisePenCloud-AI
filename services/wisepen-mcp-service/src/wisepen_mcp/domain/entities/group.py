from enum import Enum
from typing import Any

from wisepen_mcp.domain.entities.base import ApiModel


class GroupType(int, Enum):
    NORMAL_GROUP = 1
    ADVANCED_GROUP = 2
    MARKET_GROUP = 3


class GroupRoleType(int, Enum):
    OWNER = 0
    ADMIN = 1
    MEMBER = 2
    NOT_MEMBER = -1


class GroupItemInfo(ApiModel):
    group_id: str = ""
    group_name: str = ""
    group_type: GroupType | None = None
    group_desc: str = ""
    group_cover_url: str = ""
    owner_id: str = ""
    owner_info: dict[str, Any] | None = None
    member_count: int | None = None
    create_time: Any = None


class GroupMemberDetail(ApiModel):
    group_id: str = ""
    member_id: str = ""
    role: GroupRoleType | None = None
    join_time: Any = None
    token_limit: int | None = None
    token_used: int | None = None
    member_info: dict[str, Any] | None = None


class GroupResourceDailyMetric(ApiModel):
    stat_date: str = ""
    resource_added_count: int = 0
    resource_read_count: int = 0
    resource_like_count: int = 0
    resource_comment_count: int = 0
    resource_ai_call_count: int = 0


class GroupCreateRequest(ApiModel):
    group_name: str
    group_type: GroupType
    group_desc: str
    group_cover_url: str | None = None
    group_meta_info: str | None = None


__all__ = [
    "GroupCreateRequest",
    "GroupItemInfo",
    "GroupMemberDetail",
    "GroupResourceDailyMetric",
    "GroupRoleType",
    "GroupType",
]
