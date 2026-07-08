from __future__ import annotations

import hashlib
import uuid
from typing import cast

from chat.application.utils.chunking_engine import (
    Chunk,
    ChunkLocator,
    ChunkDocument,
    LocatorKind,
)
from chat.application.utils.chunking_engine.registry import get_chunking_engine
from .core.models import (
    Metadata,
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndex,
    ToolContentIndexEntry,
    ToolContentReceipt,
)
from .core.repository_protocol import ToolContentRepository

DEFAULT_TOOL_CONTENT_TTL_SECONDS = 1800
DEFAULT_TOOL_CONTENT_MAX_CHARS = 20_000_000


class ToolContentStore:
    """工具内容存储门面：原始文本 → 分块 → 持久化 → receipt。"""

    __slots__ = ("_repository",)

    def __init__(
        self,
        *,
        repository: ToolContentRepository,
    ) -> None:
        self._repository = repository

    async def put(
        self,
        *,
        session_id: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Metadata | None = None,
        chunking_engine_name: str | None = None,
        chunked: bool = True,
    ) -> ToolContentReceipt | None:
        """写入内容并返回 receipt；空文本或超长返回 None。"""
        normalized_text = text.strip()
        if not normalized_text or len(normalized_text) > DEFAULT_TOOL_CONTENT_MAX_CHARS:
            return None

        safe_metadata: Metadata = dict(metadata or {})
        safe_metadata["content_hash"] = hashlib.sha256(
            normalized_text.encode()
        ).hexdigest()

        chunks: tuple[ToolContentChunk, ...] = ()
        index = ToolContentIndex()
        chunk_metadata: Metadata = {}
        used_chunking_engine_name: str | None = None

        if chunked:
            engine_name = chunking_engine_name or (
                "markdown" if content_type == "text/markdown" else "plain_text"
            )
            chunking_engine = get_chunking_engine(engine_name)

            result = chunking_engine.chunk(
                document=ChunkDocument(
                    text=normalized_text,
                    content_type=content_type,
                    metadata=safe_metadata,
                ),
            )

            chunk_locator_view = _chunk_locator_view(result.locators)
            chunks = tuple(
                _to_tool_chunk(
                    chunk,
                    locator_view=chunk_locator_view.get(chunk.chunk_id),
                )
                for chunk in result.chunks
            )

            index = ToolContentIndex(
                entries=tuple(
                    ToolContentIndexEntry(
                        locator_name=idx.name,
                        locator_kind=idx.kind.value,
                        chunk_indices=idx.chunk_indices,
                        start_offset=idx.start_offset,
                        end_offset=idx.end_offset,
                        section_path=(
                            cast(
                                tuple[str, ...] | None, idx.metadata.get("section_path")
                            )
                            or ()
                        ),
                        page_label=cast(str | None, idx.metadata.get("page_label")),
                        anchor_label=cast(str | None, idx.metadata.get("anchor_label")),
                    )
                    for idx in result.locators
                )
            )
            chunk_metadata = dict(result.metadata)
            used_chunking_engine_name = result.pipeline

        stored = StoredToolContent(
            content_id=f"cnt_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            content_type=content_type,
            text=normalized_text,
            chunks=chunks,
            index=index,
            metadata={
                **safe_metadata,
                "chunked": chunked,
                "chunking_engine": used_chunking_engine_name,
                "chunking": chunk_metadata,
            },
        )
        await self._repository.put(stored)
        return ToolContentReceipt(
            content_id=stored.content_id,
            chunk_count=len(stored.chunks),
            supported_selectors=_selectors(stored),
        )

    async def get(
        self, *, content_id: str, session_id: str
    ) -> StoredToolContent | None:
        """按 content_id 读取；不存在或 session_id 不匹配返回 None。"""
        stored = await self._repository.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None
        return stored

    async def canonicalize_content_id(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> tuple[str, str | None]:
        """将重定向 receipt 解析为可读的 canonical content_id。"""
        stored = await self.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return content_id, None

        _REDIRECT_NOTE = (
            "The requested content_id was a redirect receipt; "
            "the readable content_id was used automatically for this call."
        )
        # canonical_content_id 优先，其次 parsed_content_id
        for key in ("canonical_content_id", "parsed_content_id"):
            target = stored.metadata.get(key)
            if isinstance(target, str) and target:
                return target, _REDIRECT_NOTE

        return content_id, None


# ---------------------------------------------------------------------------
# 模块级辅助函数
# ---------------------------------------------------------------------------


def _to_tool_chunk(
    chunk: Chunk,
    *,
    locator_view: dict[str, object] | None,
) -> ToolContentChunk:
    section_path = (
        cast(tuple[str, ...] | None, locator_view.get("section_path"))
        if locator_view
        else None
    )
    page_label = (
        cast(str | None, locator_view.get("page_label")) if locator_view else None
    )
    anchor_labels = (
        cast(tuple[str, ...] | None, locator_view.get("anchor_labels"))
        if locator_view
        else None
    )

    return ToolContentChunk(
        chunk_index=chunk.chunk_index,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        block_kinds=tuple(str(v) for v in chunk.metadata.get("block_kinds", ())),
        section_path=section_path
        or _first_section_path(chunk.metadata.get("section_paths")),
        page_label=page_label or cast(str | None, chunk.metadata.get("page_label")),
        anchor_labels=anchor_labels
        or cast(tuple[str, ...] | None, chunk.metadata.get("anchor_labels"))
        or (),
    )


def _first_section_path(value: object) -> tuple[str, ...]:
    """从 section_paths 取第一条路径；兼容嵌套 [["H1","H2"]] 和扁平 ["H1","H2"] 两种结构。"""
    if not isinstance(value, list | tuple) or not value:
        return ()
    first = value[0]
    if isinstance(first, list | tuple):
        return tuple(first)
    return tuple(value)


def _chunk_locator_view(
    locators: tuple[ChunkLocator, ...],
) -> dict[str, dict[str, object]]:
    chunk_view: dict[str, dict[str, object]] = {}

    for locator in locators:
        if locator.kind == LocatorKind.SECTION:
            section_path = cast(
                tuple[str, ...] | None, locator.metadata.get("section_path")
            )
            if not section_path:
                continue
            for chunk_id in locator.chunk_ids:
                entry = chunk_view.setdefault(chunk_id, {})
                current = entry.get("section_path")
                if not isinstance(current, tuple) or len(section_path) > len(current):
                    entry["section_path"] = section_path
            continue

        if locator.kind == LocatorKind.PAGE:
            page_label = cast(str | None, locator.metadata.get("page_label"))
            if not page_label:
                continue
            for chunk_id in locator.chunk_ids:
                chunk_view.setdefault(chunk_id, {}).setdefault("page_label", page_label)
            continue

        if locator.kind != LocatorKind.ANCHOR:
            continue

        anchor_label = cast(str | None, locator.metadata.get("anchor_label"))
        if not anchor_label:
            continue
        for chunk_id in locator.chunk_ids:
            entry = chunk_view.setdefault(chunk_id, {})
            labels = entry.setdefault("anchor_labels", [])
            if isinstance(labels, list) and anchor_label not in labels:
                labels.append(anchor_label)

    return {
        chunk_id: {
            **values,
            **(
                {"anchor_labels": tuple(values["anchor_labels"])}
                if isinstance(values.get("anchor_labels"), list)
                else {}
            ),
        }
        for chunk_id, values in chunk_view.items()
    }


def _selectors(stored: StoredToolContent) -> tuple[str, ...]:
    selectors: list[str] = []
    if stored.chunks:
        selectors.append("chunk_indices")
    if any(chunk.block_kinds for chunk in stored.chunks):
        selectors.append("block_kind")
    if stored.index is not None and stored.index.entries:
        selectors.extend(("section", "page_label", "anchor_label"))
    return tuple(selectors)
