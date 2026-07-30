from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from common.utils.chunkers import (
    Chunk,
    ChunkDocument,
    LocatorKind,
    MarkdownChunker,
    PlainTextChunker,
)

from .models import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndex,
    ToolContentIndexEntry,
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


@dataclass(slots=True)
class ChunkLocatorView:
    """汇总单个 chunk 对应的章节、页码和锚点定位信息。"""

    section_paths: list[tuple[str, ...]] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)
    anchor_labels: list[str] = field(default_factory=list)

    def add(
        self,
        *,
        kind: LocatorKind,
        section_path: tuple[str, ...],
        page_label: str | None,
        anchor_label: str | None,
    ) -> None:
        if kind is LocatorKind.SECTION:
            if section_path and section_path not in self.section_paths:
                self.section_paths.append(section_path)

        elif kind is LocatorKind.PAGE and page_label:
            if page_label not in self.page_labels:
                self.page_labels.append(page_label)

        elif (
            kind is LocatorKind.ANCHOR
            and anchor_label
            and anchor_label not in self.anchor_labels
        ):
            self.anchor_labels.append(anchor_label)


class ToolContentStore:
    """将工具输出分块，并通过仓储边界持久化。"""

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
        """写入内容；空白文本跳过，超过存储边界时明确拒绝。"""
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

        chunks, index = self._chunk(
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
            index=index,
            metadata=content_metadata,
        )
        await self._repository.put(stored)

        return ToolContentPutResult(
            status=ToolContentPutStatus.STORED,
            receipt=ToolContentReceipt(
                content_id=stored.content_id,
                chunk_count=len(chunks),
                supported_selectors=_supported_selectors(stored),
                metadata=content_metadata,
            ),
        )

    def _chunk(
        self,
        *,
        text: str,
        content_type: str,
        metadata: dict[str, object],
    ) -> tuple[tuple[ToolContentChunk, ...], ToolContentIndex]:
        """执行分块，并将通用 chunk/locator 投影为存储模型。"""
        media_type = content_type.partition(";")[0].strip().lower()
        chunker = (
            MarkdownChunker() if media_type == "text/markdown" else PlainTextChunker()
        )
        result = chunker.chunk(
            document=ChunkDocument(
                text=text,
                content_type=content_type,
                metadata=metadata,
            )
        )

        locator_views: dict[str, ChunkLocatorView] = {}
        index_entries: list[ToolContentIndexEntry] = []

        for locator in result.locators:
            raw_section_path = locator.metadata.get("section_path")
            section_path = (
                tuple(raw_section_path)
                if isinstance(raw_section_path, (list, tuple))
                and all(isinstance(item, str) for item in raw_section_path)
                else ()
            )

            raw_page_label = locator.metadata.get("page_label")
            raw_anchor_label = locator.metadata.get("anchor_label")
            page_label = raw_page_label if isinstance(raw_page_label, str) else None
            anchor_label = (
                raw_anchor_label if isinstance(raw_anchor_label, str) else None
            )

            # locator 原始范围用于 selector 检索
            index_entries.append(
                ToolContentIndexEntry(
                    locator_name=locator.name,
                    locator_kind=locator.kind.value,
                    chunk_indices=locator.chunk_indices,
                    start_offset=locator.start_offset,
                    end_offset=locator.end_offset,
                    section_path=section_path,
                    page_label=page_label,
                    anchor_label=anchor_label,
                )
            )

            # 将 locator 信息同时聚合到对应 chunk，供读取结果直接展示
            for chunk_id in locator.chunk_ids:
                locator_views.setdefault(
                    chunk_id,
                    ChunkLocatorView(),
                ).add(
                    kind=locator.kind,
                    section_path=section_path,
                    page_label=page_label,
                    anchor_label=anchor_label,
                )

        chunks = tuple(
            _to_tool_chunk(
                chunk,
                locator_views.get(chunk.chunk_id),
            )
            for chunk in result.chunks
        )
        index = ToolContentIndex(entries=tuple(index_entries))

        return chunks, index

    async def get(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> StoredToolContent | None:
        """仅返回属于当前会话的内容。"""
        stored = await self._repository.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None

        return stored


def _to_tool_chunk(
    chunk: Chunk,
    locator_view: ChunkLocatorView | None,
) -> ToolContentChunk:
    """将通用 chunk 转换为工具内容存储模型。"""
    block_kinds = chunk.metadata.get("block_kinds")

    return ToolContentChunk(
        chunk_index=chunk.chunk_index,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        source_spans=chunk.source_spans,
        block_kinds=(
            tuple(str(kind) for kind in block_kinds)
            if isinstance(block_kinds, (list, tuple))
            else ()
        ),
        section_paths=(tuple(locator_view.section_paths) if locator_view else ()),
        page_labels=(tuple(locator_view.page_labels) if locator_view else ()),
        anchor_labels=(tuple(locator_view.anchor_labels) if locator_view else ()),
    )


def _supported_selectors(
    stored: StoredToolContent,
) -> tuple[str, ...]:
    """根据实际存储的数据声明可用 selector。"""
    selectors: list[str] = []

    if stored.chunks:
        selectors.append("chunk_indices")

    if any(chunk.block_kinds for chunk in stored.chunks):
        selectors.append("block_kinds")

    locator_kinds = (
        {entry.locator_kind for entry in stored.index.entries}
        if stored.index
        else set()
    )

    if LocatorKind.SECTION.value in locator_kinds:
        selectors.append("sections")
    if LocatorKind.PAGE.value in locator_kinds:
        selectors.append("page_labels")
    if LocatorKind.ANCHOR.value in locator_kinds:
        selectors.append("anchor_labels")

    return tuple(selectors)
