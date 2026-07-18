from typing import Any, Mapping

from pydantic import BaseModel, Field


class SkillInfo(BaseModel):
    resource_id: str = Field(...)
    name: str = Field(default="")
    description: str = Field(default="")
    version: int = Field(default=0)
    source_type: str = Field(default="")

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillInfo":
        skill_info = payload.get("skillInfo") or {}
        resource_info = payload.get("resourceInfo") or {}
        return cls(
            resource_id=str(resource_info.get("resourceId") or payload.get("resourceId") or ""),
            name=str(skill_info.get("name") or ""),
            description=str(skill_info.get("description") or ""),
            version=int(skill_info.get("version") or 0),
            source_type=str(skill_info.get("sourceType") or ""),
        )


class SkillAssetUploadInitAsset(BaseModel):
    name: str = Field(...)
    path: str = Field(...)
    asset_resource_type: str = Field(...)
    md5: str = Field(...)
    expected_size: int = Field(...)

    def to_request(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "assetResourceType": self.asset_resource_type,
            "md5": self.md5,
            "expectedSize": self.expected_size,
        }


class SkillAssetUploadTicket(BaseModel):
    asset_id: str = Field(...)
    path: str = Field(...)
    name: str = Field(...)
    object_key: str = Field(...)
    put_url: str = Field(...)
    callback_header: str = Field(default="")
    flash_uploaded: bool = Field(default=False)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillAssetUploadTicket":
        return cls(
            asset_id=str(payload.get("assetId") or ""),
            path=str(payload.get("path") or ""),
            name=str(payload.get("name") or ""),
            object_key=str(payload.get("objectKey") or ""),
            put_url=str(payload.get("putUrl") or ""),
            callback_header=str(payload.get("callbackHeader") or ""),
            flash_uploaded=bool(payload.get("flashUploaded")),
        )


class SkillAssetUploadInitResult(BaseModel):
    resource_id: str = Field(...)
    version: int = Field(default=0)
    tickets: list[SkillAssetUploadTicket] = Field(default_factory=list)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillAssetUploadInitResult":
        tickets = payload.get("assetUploadTickets") or []
        return cls(
            resource_id=str(payload.get("resourceId") or ""),
            version=int(payload.get("version") or 0),
            tickets=[SkillAssetUploadTicket.from_response(item) for item in tickets],
        )
