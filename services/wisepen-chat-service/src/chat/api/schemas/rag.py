from typing import List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
)

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.enums import RagIndexingStatus


class RagResourceUpsertRequest(BaseModel):
    """API 请求：创建或更新 RAG 资源"""

    resource_id: StrictStr = Field(..., min_length=1, max_length=256, description="资源 ID")
    content: StrictStr = Field(..., min_length=1, description="资源正文内容")
    title: Optional[StrictStr] = Field(default=None, description="笔记标题")
    document_name: Optional[StrictStr] = Field(default=None, description="文档名")

    model_config = ConfigDict(extra="forbid")

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("resource_id must not contain leading or trailing whitespace")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("title", "document_name")
    @classmethod
    def validate_optional_display_field(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value != value.strip():
            raise ValueError("display field must not contain leading or trailing whitespace")
        if value == "":
            raise ValueError("display field must not be empty when provided")
        return value

class RagResourceDeleteRequest(BaseModel):
    """API 请求：删除 RAG 资源"""

    resource_id: StrictStr = Field(..., min_length=1, max_length=256, description="资源 ID")

    model_config = ConfigDict(extra="forbid")

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("resource_id must not contain leading or trailing whitespace")
        return value


class RagIndexResourceRequest(BaseModel):
    """API 请求：指定 RAG 资源"""

    resource_kind: ResourceKind = Field(..., description="资源类型：note/document")
    resource_id: StrictStr = Field(..., min_length=1, max_length=256, description="资源 ID")

    model_config = ConfigDict(extra="forbid")

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("resource_id must not contain leading or trailing whitespace")
        return value


class RagResourceDetailResponse(BaseModel):
    """API 响应：RAG 资源详情"""

    resource_id: str = Field(..., description="资源 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    version: int = Field(..., description="资源事实版本")
    content: str = Field(..., description="资源正文内容")
    is_deleted: bool = Field(..., description="是否已删除")
    indexing_status: RagIndexingStatus = Field(..., description="索引生命周期状态")
    indexing_error: Optional[str] = Field(default=None, description="最近一次索引错误")
    last_index_version: Optional[str] = Field(default=None, description="最近一次索引版本")


class RagResourceUpsertResponse(BaseModel):
    """API 响应：RAG 资源写入结果"""

    resource_id: str = Field(..., description="资源 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    resource_version: int = Field(..., description="资源事实版本")
    material_hash: str = Field(..., description="资源材料 hash")
    pipeline_version: str = Field(..., description="RAG pipeline 版本")
    index_version: str = Field(..., description="目标索引版本")
    indexing_message_published: bool = Field(..., description="是否已投递索引消息")


class RagResourceDeleteResponse(BaseModel):
    """API 响应：RAG 资源删除结果"""

    resource_id: str = Field(..., description="资源 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    deleted: bool = Field(..., description="是否删除成功")


class RagIndexManifestResponse(BaseModel):
    """API 响应：RAG Manifest"""

    resource_id: str = Field(..., description="资源 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    resource_version: int = Field(..., description="当前发布索引对应的资源版本")
    material_hash: str = Field(..., description="当前发布索引对应的材料 hash")
    pipeline_version: str = Field(..., description="当前发布索引对应的 pipeline 版本")
    current_index_version: str = Field(..., description="当前线上索引版本")


class RagIndexReadinessResponse(BaseModel):
    """API 响应：RAG 索引就绪状态"""

    resource_id: str = Field(..., description="资源 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    target_index_version: str = Field(..., description="资源当前应构建的目标索引版本")
    current_index_version: Optional[str] = Field(default=None, description="Manifest 当前发布索引版本")
    indexing_status: RagIndexingStatus = Field(..., description="索引生命周期状态")
    indexing_error: Optional[str] = Field(default=None, description="最近一次索引错误")
    last_index_version: Optional[str] = Field(default=None, description="最近一次索引版本")
    is_index_current: bool = Field(..., description="当前发布索引是否已是最新版本")
    needs_indexing: bool = Field(..., description="是否需要重新索引")
    can_retrieve_published_index: bool = Field(..., description="是否已有可检索的发布索引")
    indexing_message_published: bool = Field(..., description="是否需要或已经投递索引消息")


class RagIndexRebuildResponse(BaseModel):
    """API 响应：RAG 索引重建结果"""

    resource_id: str = Field(..., description="资源 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    resource_version: int = Field(..., description="资源事实版本")
    target_index_version: str = Field(..., description="目标索引版本")
    indexing_message_published: bool = Field(..., description="是否已投递索引消息")


class RagGcResponse(BaseModel):
    """API 响应：RAG GC 结果"""

    resource_id: str = Field(..., description="资源 ID")
    resource_kind: ResourceKind = Field(..., description="资源类型")
    kept_index_version: Optional[str] = Field(default=None, description="保留的线上索引版本")
    cleaned_index_versions: List[str] = Field(..., description="已清理的旧索引版本")
