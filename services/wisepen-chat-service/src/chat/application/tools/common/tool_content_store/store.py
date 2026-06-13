from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace

from chat.application.tools.common.chunking_engine import (
    Chunk,
    ChunkDocument,
    ChunkIndex,
    ChunkingEngine,
    ChunkingPipeline,
)
from chat.application.tools.common.chunking_engine.presets import (
    MARKDOWN_PIPELINE,
    PLAIN_TEXT_PIPELINE,
)

from .models import (
    Metadata,
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndex,
    ToolContentIndexEntry,
    ToolContentReceipt,
    ToolContentRole,
)
from .repository import ToolContentRepository


DEFAULT_TOOL_CONTENT_TTL_SECONDS = 30 * 60
DEFAULT_TOOL_CONTENT_MAX_CHARS = 20_000_000


class ToolContentStore:
    """只服务工具内容的 Store 门面。"""

    __slots__ = (
        "_repository",
        "_max_item_chars",
        "_chunking_engine",
        "_default_markdown_pipeline",
        "_default_plain_text_pipeline",
    )

    def __init__(
        self,
        *,
        repository: ToolContentRepository,
        chunking_engine: ChunkingEngine,
        max_item_chars: int = DEFAULT_TOOL_CONTENT_MAX_CHARS,
        default_markdown_pipeline: ChunkingPipeline = MARKDOWN_PIPELINE,
        default_plain_text_pipeline: ChunkingPipeline = PLAIN_TEXT_PIPELINE,
    ) -> None:
        self._repository = repository
        self._max_item_chars = max_item_chars
        self._chunking_engine = chunking_engine
        self._default_markdown_pipeline = default_markdown_pipeline
        self._default_plain_text_pipeline = default_plain_text_pipeline

    def put(
        self,
        *,
        session_id: str,
        producer: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        content_role: str | ToolContentRole = ToolContentRole.TOOL_OUTPUT,
        metadata: Metadata | None = None,
        chunking_pipeline: ChunkingPipeline | None = None,
    ) -> ToolContentReceipt | None:
        """写入工具内容并返回 receipt，不返回正文窗口。"""
        role_value = content_role.value if isinstance(content_role, ToolContentRole) else content_role
        normalized_text = text.strip()
        if not normalized_text or len(normalized_text) > self._max_item_chars:
            return None

        pipeline = chunking_pipeline or self._select_pipeline(content_type)
        safe_metadata: Metadata = dict(metadata or {})
        safe_metadata["content_hash"] = hashlib.sha256(normalized_text.encode()).hexdigest()

        result = self._chunking_engine.chunk(
            document=ChunkDocument(
                text=normalized_text,
                content_type=content_type,
                metadata=safe_metadata,
            ),
            pipeline=pipeline,
        )

        stored = StoredToolContent(
            content_id=f"cnt_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            producer=producer,
            source=source,
            content_type=content_type,
            content_role=role_value,
            text=normalized_text,
            chunks=tuple(_to_tool_chunk(chunk) for chunk in result.chunks),
            index=ToolContentIndex(
                entries=tuple(_to_index_entry(index) for index in result.indexes)
            ),
            metadata={
                **safe_metadata,
                "chunking_pipeline": result.pipeline,
                "chunking": dict(result.metadata),
            },
        )
        self._repository.put(stored)
        return _to_receipt(stored)

    def get(self, *, content_id: str, session_id: str) -> StoredToolContent | None:
        """按 content_id 读取内容实体，只校验会话作用域，不做窗口格式化。"""
        stored = self._repository.get(content_id)
        if stored is None:
            return None
        if stored.session_id != session_id:
            return None
        return stored

    def update_metadata(
        self,
        *,
        content_id: str,
        session_id: str,
        metadata: Metadata,
    ) -> StoredToolContent | None:
        """更新已存 ToolContent 的 metadata。"""
        stored = self.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return None

        updated = replace(stored, metadata={**stored.metadata, **metadata})
        self._repository.put(updated)
        return updated

    def canonicalize_content_id(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> tuple[str, str | None]:
        """解析 wrapper/window/receipt 指向的 canonical content_id。"""
        stored = self.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return content_id, None

        canonical_content_id = stored.metadata.get("canonical_content_id")
        if isinstance(canonical_content_id, str) and canonical_content_id:
            return canonical_content_id, (
                "The requested content_id was a redirect receipt; the readable "
                "content_id was used automatically for this call."
            )

        parsed_content_id = stored.metadata.get("parsed_content_id")
        if isinstance(parsed_content_id, str) and parsed_content_id:
            return parsed_content_id, (
                "The requested content_id was a redirect receipt; the readable "
                "content_id was used automatically for this call."
            )

        return content_id, None

    def _select_pipeline(self, content_type: str) -> ChunkingPipeline:
        if content_type == "text/markdown":
            return self._default_markdown_pipeline
        return self._default_plain_text_pipeline


def _to_tool_chunk(chunk: Chunk) -> ToolContentChunk:
    unit_types = tuple(str(value) for value in chunk.metadata.get("unit_types", ()))
    section_path = _first_section_path(chunk.metadata.get("section_paths"))
    return ToolContentChunk(
        chunk_id=chunk.chunk_id,
        chunk_index=chunk.chunk_index,
        level=chunk.level,
        parent_chunk_id=chunk.parent_chunk_id,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        start_unit=chunk.start_unit,
        end_unit=chunk.end_unit,
        content_hash=chunk.content_hash,
        unit_types=unit_types,
        section_path=section_path,
        anchor_names=tuple(str(value) for value in chunk.metadata.get("anchor_names", ())),
        page_name=chunk.metadata.get("page_name"),
        metadata=dict(chunk.metadata),
    )


def _to_index_entry(index: ChunkIndex) -> ToolContentIndexEntry:
    return ToolContentIndexEntry(
        name=index.name,
        kind=index.kind,
        chunk_indices=index.chunk_indices,
        chunk_ids=index.chunk_ids,
        start_offset=index.start_offset,
        end_offset=index.end_offset,
        metadata=dict(index.metadata),
    )


def _to_receipt(stored: StoredToolContent) -> ToolContentReceipt:
    index_entries = stored.index.entries if stored.index is not None else ()
    index_summary: dict[str, int] = {}
    for entry in index_entries:
        key = str(entry.kind)
        index_summary[key] = index_summary.get(key, 0) + 1

    return ToolContentReceipt(
        content_id=stored.content_id,
        producer=stored.producer,
        source=stored.source,
        content_type=stored.content_type,
        content_role=stored.content_role,
        original_length=len(stored.text),
        chunk_count=len(stored.chunks),
        index_summary=index_summary,
        read_modes=_read_modes(stored),
        selectors=_selectors(stored),
        cached=True,
        metadata=dict(stored.metadata),
    )


def _first_section_path(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not value:
        return ()
    first = value[0]
    if isinstance(first, list | tuple):
        return tuple(str(item) for item in first)
    return tuple(str(item) for item in value)


def _read_modes(stored: StoredToolContent) -> tuple[str, ...]:
    modes = ["continuous", "chunk_window"]
    if stored.index is not None and stored.index.entries:
        modes.extend(("ranked_expand", "regex_match"))
    return tuple(modes)


def _selectors(stored: StoredToolContent) -> tuple[str, ...]:
    if stored.index is None or not stored.index.entries:
        return ()
    return ("section", "page", "anchor")
