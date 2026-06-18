from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class WebContentCacheMode(StrEnum):
    """统一 URL 内容缓存访问域。"""

    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class WebContentCacheEntry:
    """Redis URL 索引。

    public 索引按 URL 共享；private 索引按 user_id + URL 隔离。
    """

    user_id: str
    url_hash: str
    canonical_url: str
    mongo_doc_id: str
    cache_mode: WebContentCacheMode
    soft_expire_at: datetime
    hard_expire_at: datetime
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class WebContentCacheValue:
    """Mongo 正文文档。

    不区分 html/file，也不区分 cleaned/parsed 阶段；只保留原始 html、最终 markdown
    和必要元信息。markdown 既可以来自清洗，也可以来自 parse。
    """

    id: str | None
    user_id: str
    canonical_url: str
    final_url: str | None
    cache_mode: WebContentCacheMode
    status_code: int | None
    content_type: str | None
    raw_html: str | None = None
    markdown: str | None = None
    content_hash: str | None = None
    fetched_at: datetime | None = None
    metadata: dict[str, object] = field(default_factory=dict)
