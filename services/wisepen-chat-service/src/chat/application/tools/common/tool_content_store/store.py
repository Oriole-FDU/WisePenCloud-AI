from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from chat.application.utils.chunkers import (
    Chunk,
    ChunkDocument,
    MarkdownChunker,
    PlainTextChunker,
    TextLocator,
)

from .models import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentReceipt,
)
from .repository import ToolContentRepository

_DEFAULT_MAX_CHARS = 20_000_000


class ToolContentPutStatus(StrEnum):
    STORED = "stored"
    EMPTY_TEXT = "empty_text"
    CONTENT_TOO_LARGE = "content_too_large"


@dataclass(frozen=True, slots=True)
class ToolContentPutResult:
    status: ToolContentPutStatus
    receipt: ToolContentReceipt | None = None
    reason: str | None = None


class ToolContentStore:
    """将工具输出投影为语义检索块和确定性原文 locator。"""

    __slots__ = ("_max_chars", "_repository")

    def __init__(
        self,
        *,
        repository: ToolContentRepository,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be greater than 0")
        self._repository = repository
        self._max_chars = max_chars

    async def put(
        self,
        *,
        session_id: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: dict[str, object] | None = None,
    ) -> ToolContentPutResult:
        if not text or text.isspace():
            return ToolContentPutResult(
                status=ToolContentPutStatus.EMPTY_TEXT,
                reason="text is empty or whitespace-only",
            )
        if len(text) > self._max_chars:
            return ToolContentPutResult(
                status=ToolContentPutStatus.CONTENT_TOO_LARGE,
                reason=f"text length {len(text)} exceeds max {self._max_chars}",
            )

        content_metadata = dict(metadata or {})
        chunks, locators = self._chunk(
            text=text,
            content_type=content_type,
            metadata=content_metadata,
        )
        stored = StoredToolContent(
            content_id=f"cnt_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            content_type=content_type,
            text=text,
            chunks=chunks,
            locators=locators,
            metadata=content_metadata,
        )
        await self._repository.put(stored)

        return ToolContentPutResult(
            status=ToolContentPutStatus.STORED,
            receipt=ToolContentReceipt(
                content_id=stored.content_id,
                chunk_count=len(chunks),
                locator_count=len(locators),
                locator_kinds=tuple(dict.fromkeys(locator.kind for locator in locators)),
                total_length=len(text),
                metadata=content_metadata,
            ),
        )

    def _chunk(
        self,
        *,
        text: str,
        content_type: str,
        metadata: dict[str, object],
    ) -> tuple[tuple[ToolContentChunk, ...], tuple[TextLocator, ...]]:
        media_type = content_type.partition(";")[0].strip().lower()
        chunker = (
            MarkdownChunker()
            if media_type == "text/markdown"
            else PlainTextChunker()
        )
        result = chunker.chunk(
            document=ChunkDocument(
                text=text,
                content_type=content_type,
                metadata=metadata,
            )
        )
        return (
            tuple(_to_tool_chunk(chunk) for chunk in result.chunks),
            result.locators,
        )

    async def get(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> StoredToolContent | None:
        stored = await self._repository.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None
        return stored


def _to_tool_chunk(chunk: Chunk) -> ToolContentChunk:
    return ToolContentChunk(
        chunk_index=chunk.chunk_index,
        source_spans=chunk.source_spans,
        section_paths=_tuple_metadata(chunk, "section_paths"),
        page_labels=_string_metadata(chunk, "page_labels"),
        anchor_labels=_string_metadata(chunk, "anchor_labels"),
    )


def _tuple_metadata(chunk: Chunk, key: str) -> tuple[tuple[str, ...], ...]:
    values = chunk.metadata.get(key)
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        tuple(str(item) for item in value if str(item))
        for value in values
        if isinstance(value, (list, tuple))
    )


def _string_metadata(chunk: Chunk, key: str) -> tuple[str, ...]:
    values = chunk.metadata.get(key)
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values if str(value))
