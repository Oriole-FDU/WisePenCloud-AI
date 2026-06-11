from dataclasses import dataclass
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
            path=str(f"{payload.get('path')}/{payload.get("name")}"),
            object_key=str(payload.get("objectKey")),
            kind=str(payload.get("skillAssetResourceType")),
            upload_status=str(payload.get("uploadStatus")),
            description=str(payload.get("description") or ""),
            size_bytes=int(payload.get("size") or 0),
        )

class Skill(BaseModel):
    skill_id: str = Field(...)
    name: str = Field(default="")
    description: str = Field(default="")
    source_type: str = Field(default="")
    skill_md_object_key: str = Field(default="")
    assets_manifest: List[SkillAssetMeta] = Field(default_factory=list)
    version: int = Field(default=0)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "Skill":
        latest_published_skill = payload.get("skillVersionBundle")
        main_skill_md = latest_published_skill.get("mainSkillMD") or {}
        return cls(
            skill_id=str(payload.get("resourceId")),
            name=str(payload.get("name") or ""),
            description=str(payload.get("description") or ""),
            source_type=str(payload.get("sourceType")),
            skill_md_object_key=str(main_skill_md.get("objectKey") or ""),
            assets_manifest=[
                SkillAssetMeta.from_response(item)
                for item in (latest_published_skill.get("skillAssets") or [])
            ],
            version=int(payload.get("version") or 0),
        )

@dataclass(frozen=True)
class SkillMeta:
    skill_id: str
    name: str
    description: str
    version: int

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "SkillMeta":
        return cls(
            skill_id=str(payload.get("resourceId")),
            name=str(payload.get("name")),
            description=str(payload.get("description")),
            version=int(payload.get("version")),
        )