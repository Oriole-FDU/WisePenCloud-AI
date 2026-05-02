from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from chat.domain.entities import (
    AttachmentLibraryStatus,
    AttachmentParseMode,
    AttachmentParseQuality,
    AttachmentParseStatus,
    AttachmentUploadStatus,
)


class InitAttachmentUploadRequest(BaseModel):
    """初始化聊天附件上传请求"""

    session_id: str = Field(..., description="当前聊天会话 ID")
    model_id: Optional[int] = Field(default=None, description="当前使用的模型 ID，不传则取默认模型")
    filename: str = Field(..., max_length=255, description="原始文件名")
    extension: str = Field(..., description="小写扩展名")
    file_size: int = Field(..., ge=0, description="文件大小，单位字节")
    md5: str = Field(..., min_length=32, max_length=32, description="文件 MD5")
    save_to_library: bool = Field(default=True, description="是否加入个人文档库")
    library_folder_id: Optional[str] = Field(default=None, description="个人文档库存储文件夹 ID，不传则使用默认文件夹")
    source: str = Field(default="chat_attachment", description="上传来源")

    @field_validator("extension")
    @classmethod
    def normalize_extension(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return (value or "").strip() or "chat_attachment"


class InitAttachmentUploadResponse(BaseModel):
    """初始化聊天附件上传响应"""

    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    upload_status: AttachmentUploadStatus = Field(..., description="上传状态")
    parse_status: AttachmentParseStatus = Field(..., description="解析状态")
    library_status: AttachmentLibraryStatus = Field(..., description="入库状态")
    save_to_library: bool = Field(..., description="当前生效的入库策略")
    library_folder_id: str = Field(default="", description="当前生效的文档库存储文件夹 ID")
    object_key: str = Field(..., description="对象存储键")
    put_url: str = Field(default="", description="直传地址")
    callback_header: str = Field(default="", description="直传回调头")
    flash_uploaded: bool = Field(default=False, description="是否命中秒传")


class CompleteAttachmentUploadRequest(BaseModel):
    """回执聊天附件上传完成请求"""

    session_id: str = Field(..., description="当前聊天会话 ID")
    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    object_key: Optional[str] = Field(default=None, description="上传完成后的对象存储键")


class CompleteAttachmentUploadResponse(BaseModel):
    """回执聊天附件上传完成响应"""

    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    upload_status: AttachmentUploadStatus = Field(..., description="上传状态")
    parse_status: AttachmentParseStatus = Field(..., description="解析状态")
    object_key: str = Field(..., description="对象存储键")


class QueryAttachmentRequest(BaseModel):
    """查询聊天附件回显请求"""

    session_id: str = Field(..., description="当前聊天会话 ID")
    attachment_ids: List[str] = Field(default_factory=list, description="附件 ID 列表")


class AttachmentItemResponse(BaseModel):
    """单个聊天附件回显项"""

    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    filename: str = Field(..., description="原始文件名")
    extension: str = Field(..., description="小写扩展名")
    upload_status: AttachmentUploadStatus = Field(..., description="上传状态")
    parse_status: AttachmentParseStatus = Field(..., description="解析状态")
    context_enabled: bool = Field(..., description="是否参与后续轮次上下文")
    save_to_library: bool = Field(..., description="是否加入个人文档库")
    library_status: AttachmentLibraryStatus = Field(..., description="入库状态")
    summary: str = Field(default="", description="附件摘要")
    content_excerpt: str = Field(default="", description="正文预览")
    parse_mode: Optional[AttachmentParseMode] = Field(default=None, description="解析方式")
    parse_quality: Optional[AttachmentParseQuality] = Field(default=None, description="解析质量")
    error_message: str = Field(default="", description="失败原因")
    resource_id: str = Field(default="", description="个人文档库资源 ID")
    library_folder_id: str = Field(default="", description="个人文档库存储文件夹 ID")


class QueryAttachmentResponse(BaseModel):
    """聊天附件查询响应"""

    attachments: List[AttachmentItemResponse] = Field(default_factory=list, description="附件列表")


class CheckAttachmentUploadCapabilityRequest(BaseModel):
    """附件上传能力预检请求"""

    model_id: Optional[int] = Field(default=None, description="当前使用的模型 ID，不传则取默认模型")
    extension: str = Field(..., description="待上传文件的小写扩展名")

    @field_validator("extension")
    @classmethod
    def normalize_capability_extension(cls, value: str) -> str:
        return (value or "").strip().lower()

class CheckAttachmentUploadCapabilityResponse(BaseModel):
    """附件上传能力预检响应"""

    allowed: bool = Field(..., description="是否允许上传")
    is_image: bool = Field(..., description="是否为图像文件")
    model_id: int = Field(..., description="实际参与校验的模型 ID")
    support_vision: bool = Field(..., description="模型是否支持视觉能力")
    reason: str = Field(default="", description="不允许上传时的原因")

class UpdateAttachmentConfigRequest(BaseModel):
    """更新聊天附件配置请求"""

    session_id: str = Field(..., description="当前聊天会话 ID")
    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    save_to_library: bool = Field(..., description="是否加入个人文档库")
    context_enabled: bool = Field(..., description="是否参与后续轮次上下文")
    library_folder_id: Optional[str] = Field(default=None, description="个人文档库存储文件夹 ID，不传则沿用原值")


class UpdateAttachmentConfigResponse(BaseModel):
    """更新聊天附件配置响应"""

    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    save_to_library: bool = Field(..., description="当前生效的入库策略")
    context_enabled: bool = Field(..., description="当前是否参与后续轮次上下文")
    library_folder_id: str = Field(default="", description="当前生效的文档库存储文件夹 ID")
    library_status: AttachmentLibraryStatus = Field(..., description="当前入库状态")
    effective_from: str = Field(..., description="配置生效时机")


class ReportAttachmentParseResultRequest(BaseModel):
    """回执聊天附件解析结果请求"""

    session_id: str = Field(..., description="当前聊天会话 ID")
    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    success: bool = Field(..., description="解析是否成功")
    summary: str = Field(default="", description="附件摘要")
    content_excerpt: str = Field(default="", description="正文预览")
    extracted_text: str = Field(default="", description="解析全文文本")
    error_message: str = Field(default="", description="失败原因")


class ReportAttachmentParseResultResponse(BaseModel):
    """回执聊天附件解析结果响应"""

    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    parse_status: AttachmentParseStatus = Field(..., description="解析状态")
    parse_mode: Optional[AttachmentParseMode] = Field(default=None, description="解析方式")
    parse_quality: Optional[AttachmentParseQuality] = Field(default=None, description="解析质量")
    context_enabled: bool = Field(..., description="是否已启用上下文")


class RemoveAttachmentRequest(BaseModel):
    """移除聊天附件请求"""

    session_id: str = Field(..., description="当前聊天会话 ID")
    attachment_id: str = Field(..., description="聊天附件唯一 ID")


class RemoveAttachmentResponse(BaseModel):
    """移除聊天附件响应"""

    attachment_id: str = Field(..., description="聊天附件唯一 ID")
    upload_status: AttachmentUploadStatus = Field(..., description="上传状态")
    parse_status: AttachmentParseStatus = Field(..., description="解析状态")
    context_enabled: bool = Field(..., description="是否仍参与后续轮次上下文")
