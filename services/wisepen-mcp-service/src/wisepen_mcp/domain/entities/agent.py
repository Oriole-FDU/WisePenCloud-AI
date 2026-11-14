from enum import Enum
from typing import Any, Mapping

from pydantic import Field

from wisepen_mcp.domain.entities.base import ApiModel


class AIResourceSourceType(str, Enum):
    BY_AGENT = "BY_AGENT"
    MANUAL = "MANUAL"


class VersionStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class AgentInfo(ApiModel):
    resource_id: str = ""
    name: str = ""
    description: str = ""
    version: int = 0
    source_type: AIResourceSourceType | None = None

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "AgentInfo":
        return cls.model_validate(payload.get("agentInfo") or {})


class AgentVersionBundleInfo(ApiModel):
    resource_id: str = ""
    version: int | None = None
    status: VersionStatus | None = None
    spec: dict[str, Any] | None = None
    assets: list[Any] = Field(default_factory=list)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "AgentVersionBundleInfo":
        return cls.model_validate(payload)


class AgentSpecUpdateRequest(ApiModel):
    resource_id: str
    draft_version: int
    spec: dict[str, Any]


__all__ = [
    "AIResourceSourceType",
    "AgentInfo",
    "AgentSpecUpdateRequest",
    "AgentVersionBundleInfo",
    "VersionStatus",
]


