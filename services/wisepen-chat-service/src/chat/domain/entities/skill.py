from typing import Any, List, Mapping

from pydantic import BaseModel, Field


class SkillAssetMeta(BaseModel):
    id: str = Field(...)
    path: str = Field(...)
    object_key: str = Field(...)
    kind: str = Field(...)
    upload_status: str = Field(...)
    description: str | None = None
    size_bytes: int = Field(default=0)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillAssetMeta":
        return cls(
            id=str(payload.get("id")),
            path=str(f"{payload.get('path').rstrip('/')}/{payload.get('name')}"),
            object_key=str(payload.get("objectKey")),
            kind=str(payload.get("assetResourceType")),
            upload_status=str(payload.get("uploadStatus")),
            description=str(payload.get("description") or ""),
            size_bytes=int(payload.get("size") or 0),
        )


class Skill(BaseModel):
    skill_id: str = Field(...)
    name: str = Field(default="")
    description: str = Field(default="")
    source_type: str = Field(default="")
    assets_manifest: List[SkillAssetMeta] = Field(default_factory=list)
    version: int = Field(default=0)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "Skill":
        latest_published_skill = payload.get("skillVersionBundle") or {}
        return cls(
            skill_id=str(payload.get("resourceId")),
            name=str(payload.get("name") or ""),
            description=str(payload.get("description") or ""),
            source_type=str(payload.get("sourceType")),
            assets_manifest=[
                SkillAssetMeta.from_response(item)
                for item in (latest_published_skill.get("assets") or [])
            ],
            version=int(payload.get("version") or 0),
        )


class SkillMeta(BaseModel):
    skill_id: str = Field(...)
    name: str = Field(default="")
    description: str = Field(default="")
    version: int = Field(default=0)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillMeta":
        return cls(
            skill_id=str(payload.get("resourceId")),
            name=str(payload.get("name")),
            description=str(payload.get("description")),
            version=int(payload.get("version") or 0),
        )


class SkillInfo(BaseModel):
    resource_id: str = Field(...)
    name: str = Field(default="")
    description: str = Field(default="")
    version: int = Field(default=0)
    source_type: str = Field(...)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillInfo":
        skill_info = payload.get("skillInfo") or {}
        resource_info = payload.get("resourceInfo") or {}
        return cls(
            resource_id=str(resource_info.get("resourceId") or payload.get("resourceId") or ""),
            name=str(skill_info.get("name") or ""),
            description=str(skill_info.get("description") or ""),
            version=int(skill_info.get("version") or 0),
            source_type=str(skill_info.get("sourceType")),
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
    callback_header: str = Field(...)
    flash_uploaded: bool = Field(default=False)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillAssetUploadTicket":
        return cls(
            asset_id=str(payload.get("assetId")),
            path=str(payload.get("path")),
            name=str(payload.get("name")),
            object_key=str(payload.get("objectKey")),
            put_url=str(payload.get("putUrl")),
            callback_header=str(payload.get("callbackHeader")),
            flash_uploaded=bool(payload.get("flashUploaded")),
        )


class SkillAssetUploadInitResult(BaseModel):
    resource_id: str = Field(...)
    version: int = Field(default=0)
    tickets: List[SkillAssetUploadTicket] = Field(default_factory=list)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillAssetUploadInitResult":
        tickets = payload.get("assetUploadTickets") or []
        return cls(
            resource_id=str(payload.get("resourceId") or ""),
            version=int(payload.get("version") or 0),
            tickets=[SkillAssetUploadTicket.from_response(item) for item in tickets],
        )
