"""工具正文的纯函数 Store 边界。

本模块只负责文本校验、分块和 receipt 组装；Redis client、key 和 TTL 由
``RedisToolContentRepository`` 自己声明，调用方不需要注入任何缓存对象。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

from common.utils.document import (
    Anchor,
    DocumentChunker,
    Page,
    Section,
    SourceSpan,
)

_DEFAULT_MAX_CHARS = 20_000_000


@lru_cache(maxsize=1)
def _repository():
    """惰性创建 Redis 仓储，避免业务层持有依赖。"""

    from chat.core.persistence.redis.tool_content_repository import (
        RedisToolContentRepository,
    )

    return RedisToolContentRepository()


@dataclass(frozen=True, slots=True)
class ToolContentChunk:
    """缓存索引保存已切好的正文 chunk、原文边界和结构身份。"""

    text: str
    chunk_index: int
    start_offset: int
    end_offset: int
    source_spans: tuple[SourceSpan, ...]
    section_ids: tuple[str, ...] = ()
    page_labels: tuple[str, ...] = ()
    anchor_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredToolContent:
    """一个会话内可检索、可按结构确定性读取的完整工具正文。"""

    content_id: str
    session_id: str
    text: str
    chunks: tuple[ToolContentChunk, ...] = ()
    sections: tuple[Section, ...] = ()
    pages: tuple[Page, ...] = ()
    anchors: tuple[Anchor, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentReceipt:
    content_id: str
    chunk_count: int
    total_length: int


async def put_tool_content(
    *,
    session_id: str,
    text: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> ToolContentReceipt | None:
    """分块并持久化正文；空白或超限正文不进入 Redis。"""

    if max_chars < 1:
        raise ValueError("max_chars must be greater than 0")
    if not text or text.isspace() or len(text) > max_chars:
        return None

    result = DocumentChunker().chunk(text)
    stored = StoredToolContent(
        content_id=f"cnt_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        text=text,
        chunks=tuple(
            ToolContentChunk(
                text=chunk.text,
                chunk_index=chunk.chunk_index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                source_spans=chunk.source_spans,
                section_ids=chunk.section_ids,
                page_labels=chunk.page_labels,
                anchor_labels=chunk.anchor_labels,
            )
            for chunk in result.chunks
        ),
        sections=result.sections,
        pages=result.pages,
        anchors=result.anchors,
    )
    await _repository().put(stored)
    return ToolContentReceipt(
        content_id=stored.content_id,
        chunk_count=len(stored.chunks),
        total_length=len(text),
    )


async def get_tool_content(
    *,
    content_id: str,
    session_id: str,
) -> StoredToolContent | None:
    """读取正文并强制执行会话归属校验。"""

    stored = await _repository().get(content_id)
    if stored is None or stored.session_id != session_id:
        return None
    return stored
