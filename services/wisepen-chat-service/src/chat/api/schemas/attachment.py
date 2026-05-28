from typing import Optional
from pydantic import BaseModel, Field


class InitLargeUploadRequest(BaseModel):
    """大文件上传初始化请求（前端直传 OSS）"""
    session_id: str = Field(..., description="会话 ID")
    filename: str = Field(..., description="文件名")
    extension: str = Field(..., description="文件扩展名")
    file_size: int = Field(..., description="文件大小（字节）")


class InitLargeUploadResponse(BaseModel):
    """大文件上传初始化响应"""
    object_key: str = Field(..., description="OSS 对象键")
    put_url: str = Field(default="", description="上传直传 URL")
    callback_header: str = Field(default="", description="回调认证头")


class UploadSmallResponse(BaseModel):
    """小文件上传响应"""
    object_key: str = Field(..., description="OSS 对象键")


class DeleteAttachmentRequest(BaseModel):
    object_key: str = Field(..., description="OSS 对象键")


class DeleteAttachmentResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")


class GetAttachmentPreviewUrlResponse(BaseModel):
    url: str = Field(..., description="预览下载 URL")
