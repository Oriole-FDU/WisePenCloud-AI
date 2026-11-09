"""工具正文的纯函数 Store 边界。

本模块只负责文本校验、Markdown 结构提取和 receipt 组装；Redis client、key 和 TTL 由
``RedisToolContentRepository`` 自己声明，调用方不需要注入任何缓存对象。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from common.utils.markdown import (
    Anchor,
    MarkdownChunk,
    MarkdownChunker,
    Section,
)

_DEFAULT_MAX_CHARS = 20_000_000


@lru_cache(maxsize=1)
def _repository():
    """惰性创建 Redis 仓储，避免业务层持有依赖。"""
    from chat.core.persistence.redis.tool_content_repository import (
        RedisToolContentRepository,
    )

    return RedisToolContentRepository()


@dataclass(slots=True)
class StoredToolContent:
    """一个会话内的原始工具正文及其可重建结构。"""

    content_id: str
    session_id: str
    text: str
    sections: list[Section] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)
    sections_by_id: dict[str, Section] = field(default_factory=dict)
    chunks: list[MarkdownChunk] = field(default_factory=list)

    def __post_init__(self) -> None:
        """建立按 ID 的索引；旧缓存缺少索引时也能在读取后补齐。"""
        # section_id 是章节的稳定身份，读取章节时应直接定位而不是重复扫描列表。
        self.sections_by_id = {
            section.section_id: section for section in self.sections
        }


async def put_tool_content(
    *,
    session_id: str,
    text: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> tuple[str, int] | None:
    """解析并持久化正文和结构；空白或超限正文不进入 Redis。"""

    if max_chars < 1:
        raise ValueError("max_chars must be greater than 0")
    if not text or text.isspace() or len(text) > max_chars:
        return None  # 空白或超限直接跳过

    result = MarkdownChunker().chunk(text)
    stored = StoredToolContent(
        content_id=f"cnt_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        text=text,
        chunks=result.chunks,
        sections=result.sections,
        anchors=result.anchors,
    )
    await _repository().put(stored)
    return stored.content_id, len(text)


async def get_tool_content(
    *,
    content_id: str,
    session_id: str,
) -> StoredToolContent | None:
    """读取正文并强制执行会话归属校验。"""

    stored = await _repository().get(content_id)
    if stored is None or stored.session_id != session_id:  # 校验会话归属
        return None
    return stored
