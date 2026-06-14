from typing import Optional
from pydantic import BaseModel, Field


class InitUploadRequest(BaseModel):
    """附件直传初始化请求（纯元数据，无文件字节）"""
    session_id: str = Field(..., description="会话 ID")
    filename: str = Field(..., min_length=1, max_length=255, description="文件名")
    extension: str = Field(..., min_length=1, max_length=32, pattern=r'^[a-zA-Z0-9]+$', description="文件后缀（不含点）")
    file_size: int = Field(..., gt=0, le=104857600, description="文件大小（字节），上限 100MB")
    md5: str = Field(..., min_length=32, max_length=32, pattern=r'^[a-fA-F0-9]{32}$', description="文件 MD5")
    enable_library: bool = Field(default=False, description="是否同时入库文档库")


class InitUploadResponse(BaseModel):
    """附件直传初始化响应"""
    object_key: str = Field(..., description="OSS object key")
    put_url: str = Field(..., description="OSS 预签名上传 URL")
    callback_header: str = Field(default="", description="OSS 回调 header")


class DeleteAttachmentRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    filename: str = Field(..., description="文件名")


class DeleteAttachmentResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
