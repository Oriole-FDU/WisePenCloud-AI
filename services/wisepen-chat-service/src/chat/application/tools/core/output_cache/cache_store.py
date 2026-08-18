from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from chat.domain.repositories import ToolContentRepository
from common.utils.document import (
    Anchor,
    DocumentChunker,
    Page,
    Section,
    SourceSpan,
)

_DEFAULT_MAX_CHARS = 20_000_000


@dataclass(frozen=True, slots=True)
class ToolContentChunk:
    """缓存索引只保存回源范围和确定性结构身份。"""

    chunk_index: int
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
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContentReceipt:
    content_id: str
    chunk_count: int
    total_length: int
    metadata: dict[str, object] = field(default_factory=dict)


class ToolContentStore:
    """统一解析并持久化模型后续可以检索和读取的工具正文。"""

    __slots__ = ("_max_chars", "_tool_content_repository")

    def __init__(
        self,
        *,
        tool_content_repository: ToolContentRepository,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be greater than 0")
        self._tool_content_repository = tool_content_repository
        self._max_chars = max_chars

    async def put(
        self,
        *,
        session_id: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> ToolContentReceipt | None:
        if not text or text.isspace() or len(text) > self._max_chars:
            return None

        result = DocumentChunker().chunk(text)
        content_metadata = dict(metadata or {})
        stored = StoredToolContent(
            content_id=f"cnt_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            text=text,
            chunks=tuple(
                ToolContentChunk(
                    chunk_index=chunk.chunk_index,
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
            metadata=content_metadata,
        )
        await self._tool_content_repository.put(stored)

        return ToolContentReceipt(
            content_id=stored.content_id,
            chunk_count=len(stored.chunks),
            total_length=len(text),
            metadata=content_metadata,
        )

    async def get(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> StoredToolContent | None:
        stored = await self._tool_content_repository.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None
        return stored
