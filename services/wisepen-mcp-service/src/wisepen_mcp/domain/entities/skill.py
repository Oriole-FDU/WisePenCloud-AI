from enum import Enum
from typing import Any, Mapping

from pydantic import Field

from wisepen_mcp.domain.entities.base import ApiModel

class AIResourceSourceType(str, Enum):
    BY_AGENT = "BY_AGENT"
    MANUAL = "MANUAL"


class SkillInfo(ApiModel):
    resource_id: str = ""
    name: str = ""
    description: str = ""
    version: int = 0
    source_type: AIResourceSourceType | None = None

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillInfo":
        return cls.model_validate(payload.get("skillInfo") or {})


class SkillAssetUploadInitAsset(ApiModel):
    name: str
    path: str
    asset_resource_type: str
    md5: str
    expected_size: int

    def to_request(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class SkillAssetUploadTicket(ApiModel):
    asset_id: str = ""
    path: str = ""
    name: str = ""
    object_key: str = ""
    put_url: str = ""
    callback_header: str = ""
    flash_uploaded: bool = False

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillAssetUploadTicket":
        return cls.model_validate(payload)


class SkillAssetUploadInitResult(ApiModel):
    resource_id: str = ""
    version: int = 0
    tickets: list[SkillAssetUploadTicket] = Field(default_factory=list, alias="assetUploadTickets")

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillAssetUploadInitResult":
        return cls.model_validate(payload)


__all__ = [
    "AIResourceSourceType",
    "SkillAssetUploadInitAsset",
    "SkillAssetUploadInitResult",
    "SkillAssetUploadTicket",
    "SkillInfo",
]
