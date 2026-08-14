from typing import Any, Mapping

from pydantic import BaseModel, Field


# 预留：Agent 资产元数据目前仅映射 Java 版本包，尚未进入 Chat 使用链路。
class AgentAssetMeta(BaseModel):
    """Agent version bundle asset metadata returned by Java AI Asset service."""

    id: str = Field(...)
    path: str = Field(...)
    object_key: str = Field(...)
    kind: str = Field(...)
    upload_status: str = Field(...)
    description: str | None = None
    size_bytes: int = Field(default=0)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "AgentAssetMeta":
        return cls(
            id=str(payload.get("id")),
            path=str(f"{payload.get('path').rstrip('/')}/{payload.get('name')}"),
            object_key=str(payload.get("objectKey")),
            kind=str(payload.get("assetResourceType")),
            upload_status=str(payload.get("uploadStatus")),
            description=str(payload.get("description") or ""),
            size_bytes=int(payload.get("size") or 0),
        )
