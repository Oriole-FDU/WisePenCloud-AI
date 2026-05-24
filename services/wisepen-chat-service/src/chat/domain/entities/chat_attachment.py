from datetime import datetime, timezone
from typing import Optional
from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING


class ChatAttachment(Document):
    """会话附件记录，跟踪文件从 pending → uploaded → deleted 的完整生命周期"""

    session_id: str # 附件所属会话ID
    user_id: str # 附件所属用户ID
    object_key: str # OSS对象键
    original_name: str # 原始文件名
    extension: str # 文件扩展名
    file_size: int # 文件大小，单位字节
    mime_type: Optional[str] = None # 文件MIME类型
    status: str = "pending" # 附件状态，pending/uploaded/deleted
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # 创建时间
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # 更新时间

    class Settings:
        name = "wisepen_chat_attachment"
        indexes = [
            IndexModel([("session_id", ASCENDING), ("status", ASCENDING)]), # 会话ID+状态索引，用于快速查询会话下的所有文件
            IndexModel([("user_id", ASCENDING), ("session_id", ASCENDING)]), # 用户ID+会话ID索引，用于快速查询用户下的所有文件
        ]
