from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel


class AttachmentUploadStatus(str, Enum):
    WAIT_UPLOAD = "WAIT_UPLOAD"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class AttachmentParseStatus(str, Enum):
    WAITING = "WAITING"
    PARSING = "PARSING"
    READY = "READY"
    FAILED = "FAILED"


class AttachmentLibraryStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING_SAVE = "PENDING_SAVE"
    SAVING = "SAVING"
    SAVED = "SAVED"
    INDEXING = "INDEXING"
    INDEX_READY = "INDEX_READY"
    INDEX_FAILED = "INDEX_FAILED"


class AttachmentContextMode(str, Enum):
    SUMMARY = "SUMMARY"
    AUTO_CHUNK = "AUTO_CHUNK"


class AttachmentParseMode(str, Enum):
    DOCUMENT_TEXT = "DOCUMENT_TEXT"


class AttachmentParseQuality(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class AttachmentChunk(BaseModel):
    """附件解析后的切片"""

    chunk_id: str = Field(..., description="附件内切片唯一标识")
    sequence: int = Field(..., description="切片顺序")
    text: str = Field(..., description="切片文本")


class AttachmentContext(BaseModel):
    """
    聊天服务可直接消费的标准附件上下文对象
    """

    attachment_id: str
    filename: str
    parse_mode: AttachmentParseMode
    parse_quality: AttachmentParseQuality
    summary: str = ""
    extracted_text: str = ""
    chunks: List[AttachmentChunk] = Field(default_factory=list)
    error_message: Optional[str] = None
    disabled: bool = False


class ChatAttachment(Document):
    """
    聊天会话级附件记录
    """

    attachment_id: str = Field(..., description="返回给前端的附件唯一 ID")
    user_id: str = Field(..., description="附件所属用户 ID")
    session_id: str = Field(..., description="附件所属会话 ID")

    filename: str = Field(..., description="原始文件名")
    extension: str = Field(..., description="小写扩展名")
    file_size: int = Field(..., ge=0, description="文件大小，单位字节")
    md5: str = Field(..., description="前端上传的 MD5，用于秒传判定")
    source: str = Field(default="chat_attachment", description="上传来源标记")

    object_key: Optional[str] = Field(default=None, description="对象存储键")
    resource_id: Optional[str] = Field(default=None, description="个人文档库资源 ID")
    library_folder_id: Optional[str] = Field(default=None, description="个人文档库存储文件夹 ID")

    upload_status: AttachmentUploadStatus = Field(default=AttachmentUploadStatus.WAIT_UPLOAD)
    parse_status: AttachmentParseStatus = Field(default=AttachmentParseStatus.WAITING)
    library_status: AttachmentLibraryStatus = Field(default=AttachmentLibraryStatus.PENDING_SAVE)

    save_to_library: bool = Field(default=True, description="是否加入个人文档库")
    context_enabled: bool = Field(default=False, description="是否参与后续轮次上下文")

    parse_mode: Optional[AttachmentParseMode] = Field(default=None)
    parse_quality: Optional[AttachmentParseQuality] = Field(default=None)
    summary: str = Field(default="", description="附件摘要")
    content_excerpt: str = Field(default="", description="返回给前端的正文预览")
    extracted_text: str = Field(default="", description="解析得到的全文文本")
    chunks: List[AttachmentChunk] = Field(default_factory=list)
    error_message: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parsed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    class Settings:
        name = "wisepen_chat_attachment"
        indexes = [
            # 前端与服务端均通过 attachment_id 做主查询
            IndexModel([("attachment_id", ASCENDING)], unique=True),
            # 会话附件托盘与会话回显按用户 + 会话维度查询
            IndexModel([("user_id", ASCENDING), ("session_id", ASCENDING), ("created_at", DESCENDING)]),
            # 便于后续筛选 READY 且启用的附件进入上下文组装
            IndexModel([("session_id", ASCENDING), ("parse_status", ASCENDING), ("context_enabled", ASCENDING)]),
        ]
