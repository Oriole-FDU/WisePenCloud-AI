from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from chat.application.tools.web_tools.web_content_cache.models import WebContentCacheMode


class WebContentCacheValueDocument(Document):
    """统一 URL 内容缓存正文。

    Redis 只保存 URL 到本文档 ID 的索引；正文内容、最终 Markdown 和元信息放在
    MongoDB，便于后续按版本、来源和过期策略扩展。
    """

    user_id: str = Field(..., description="缓存归属用户 ID；public 内容记录首次写入用户")
    canonical_url: str = Field(..., description="缓存索引用的规范化 URL")
    final_url: str | None = Field(default=None, description="实际抓取后的最终 URL")
    cache_mode: WebContentCacheMode = Field(..., description="缓存访问域")
    status_code: int | None = Field(default=None, description="抓取响应状态码")
    content_type: str | None = Field(default=None, description="响应内容类型")
    raw_html: str | None = Field(default=None, description="原始 HTML")
    markdown: str | None = Field(default=None, description="清洗或解析得到的 Markdown")
    content_hash: str | None = Field(default=None, description="正文内容 hash")
    fetched_at: datetime | None = Field(default=None, description="内容抓取时间")
    metadata: dict[str, Any] = Field(default_factory=dict, description="缓存元信息")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_web_content_cache_values"
        indexes = [
            IndexModel(
                [("cache_mode", ASCENDING), ("canonical_url", ASCENDING), ("updated_at", DESCENDING)],
                name="idx_web_content_cache_mode_url_updated",
            ),
            IndexModel(
                [("cache_mode", ASCENDING), ("user_id", ASCENDING), ("canonical_url", ASCENDING)],
                name="idx_web_content_cache_mode_user_url",
            ),
            IndexModel(
                [("content_hash", ASCENDING)],
                name="idx_web_content_cache_content_hash",
            ),
            IndexModel(
                [("fetched_at", DESCENDING)],
                name="idx_web_content_cache_fetched_at",
            ),
        ]
