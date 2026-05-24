from typing import Optional
from pydantic import BaseModel, Field


class InitAttachmentUploadRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    filename: str = Field(..., description="文件名")
    extension: str = Field(..., description="文件扩展名")
    file_size: int = Field(..., description="文件大小（字节）")
    md5: str = Field(..., description="文件 MD5")
    mime_type: Optional[str] = Field(default=None, description="文件 MIME 类型")


class InitAttachmentUploadResponse(BaseModel):
    object_key: str = Field(..., description="OSS 对象键")
    put_url: str = Field(default="", description="上传直传 URL（秒传时为空）")
    callback_header: str = Field(default="", description="回调认证头")
    flash_uploaded: bool = Field(default=False, description="是否触发秒传（同 MD5 文件已存在，无需前端 PUT）")


class ConfirmUploadRequest(BaseModel):
    object_key: str = Field(..., description="OSS 对象键")


class ConfirmUploadResponse(BaseModel):
    object_key: str = Field(..., description="OSS 对象键")
    status: str = Field(..., description="附件上传状态")


class DeleteAttachmentRequest(BaseModel):
    object_key: str = Field(..., description="OSS 对象键")


class DeleteAttachmentResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")


class GetAttachmentPreviewUrlResponse(BaseModel):
    url: str = Field(..., description="预览下载 URL")
