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
    """ToolContentStore.put 的结果状态。"""

    STORED = "stored"
    EMPTY_TEXT = "empty_text"
    CONTENT_TOO_LARGE = "content_too_large"


@dataclass(frozen=True, slots=True)
class ToolContentPutResult:
    """写入工具内容后的结果，以及可选的后续读取凭证。"""

    status: ToolContentPutStatus
    receipt: ToolContentReceipt | None = None
    reason: str | None = None


class ToolContentStore:
    """将工具输出持久化为可回读的正文、chunk 和 locator。

    这里是缓存链的中心：`ToolOutputCache` 把工具正文交给它，它负责
    生成稳定 `content_id`、切分 chunk、保留结构 locator，并把完整原文交给
    repository。后续的 page/section/semantic/range 读取都只认这个 content_id。
    """

    __slots__ = ("_max_chars", "_repository")

    def __init__(
        self,
        *,
        repository: ToolContentRepository,
        max_chars: int = _DEFAULT_MAX_CHARS,
    ) -> None:
        """创建持久化存储。

        `max_chars` 保护入库边界，避免把超大正文写入缓存后再在读取阶段才失败。
        """

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
        """校验并持久化一段工具正文。

        返回 `EMPTY_TEXT` 和 `CONTENT_TOO_LARGE` 时不生成 receipt；只有真正写入
        repository 后，调用方才会拿到可供 session tools 读取的 `content_id`。
        """

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

        # metadata 会同时进入存储实体、chunk 元数据和入库回执，避免各层各自
        # 维护一份略有差异的来源信息。
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
                # locator_kinds 直接来自实际存入的 locator，供上层判断是否有
                # page/section/anchor 等结构入口。
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
        """根据内容类型选择 chunker，并把通用 chunk 结果转成 ToolContent 模型。"""

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
        """按 content_id 读取，并用 session_id 做隔离校验。

        repository 只认识 content_id；session_id 是这层补上的访问边界，用来
        防止不同会话猜中同一个 id 后跨会话读取。
        """

        stored = await self._repository.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None
        return stored


def _to_tool_chunk(chunk: Chunk) -> ToolContentChunk:
    """把通用 chunk 转成 ToolContentStore 所需的持久化 chunk。"""

    return ToolContentChunk(
        chunk_index=chunk.chunk_index,
        source_spans=chunk.source_spans,
        section_paths=_tuple_metadata(chunk, "section_paths"),
        page_labels=_string_metadata(chunk, "page_labels"),
        anchor_labels=_string_metadata(chunk, "anchor_labels"),
    )


def _tuple_metadata(chunk: Chunk, key: str) -> tuple[tuple[str, ...], ...]:
    """读取 chunk.metadata 中的二维字符串元数据。"""

    values = chunk.metadata.get(key)
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        tuple(str(item) for item in value if str(item))
        for value in values
        if isinstance(value, (list, tuple))
    )


def _string_metadata(chunk: Chunk, key: str) -> tuple[str, ...]:
    """读取 chunk.metadata 中的一维字符串元数据。"""

    values = chunk.metadata.get(key)
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values if str(value))
