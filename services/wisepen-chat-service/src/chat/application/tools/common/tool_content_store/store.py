from __future__ import annotations

import hashlib
import uuid
from typing import Protocol

from chat.application.utils.chunking_engine import (
    Chunk,
    ChunkDocument,
    ChunkingEngine,
)
from chat.application.utils.chunking_engine.registry import (
    MARKDOWN_PIPELINE_NAME,
    PLAIN_TEXT_PIPELINE_NAME,
    get_chunking_pipeline,
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

DEFAULT_TOOL_CONTENT_TTL_SECONDS = 30 * 60
DEFAULT_TOOL_CONTENT_MAX_CHARS = 20_000_000
_DEFAULT_CHUNKING_ENGINE = ChunkingEngine()


class ToolContentRepository(Protocol):
    """ToolContent 持久化仓储协议（接口），定义 put/get 两个核心操作。"""

    async def put(self, stored: StoredToolContent) -> None:
        """写入 ToolContent。"""
        ...

    async def get(self, content_id: str) -> StoredToolContent | None:
        """按 content_id 读取 ToolContent，不存在则返回 None。"""
        ...


class ToolContentStore:
    """工具内容存储门面：原始文本 → 分块 → 持久化 → receipt。"""

    __slots__ = ("_repository", "_chunking_engine")

    def __init__(
        self,
        *,
        repository: ToolContentRepository,
        chunking_engine: ChunkingEngine | None = None,
    ) -> None:
        self._repository = repository
        self._chunking_engine = chunking_engine or _DEFAULT_CHUNKING_ENGINE

    async def put(
        self,
        *,
        session_id: str,
        producer: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        content_role: str | ToolContentRole = ToolContentRole.TOOL_OUTPUT,
        metadata: Metadata | None = None,
        chunking_pipeline_name: str | None = None,
        chunked: bool = True,
    ) -> ToolContentReceipt | None:
        """写入内容并返回 receipt；空文本或超长返回 None。"""
        role_value = content_role.value if isinstance(content_role, ToolContentRole) else content_role
        normalized_text = text.strip()
        if not normalized_text or len(normalized_text) > DEFAULT_TOOL_CONTENT_MAX_CHARS:
            return None

        safe_metadata: Metadata = dict(metadata or {})
        safe_metadata["content_hash"] = hashlib.sha256(normalized_text.encode()).hexdigest()

        chunks: tuple[ToolContentChunk, ...] = ()
        index = ToolContentIndex()
        chunk_metadata: Metadata = {}
        used_chunking_pipeline_name: str | None = None

        if chunked:
            pipeline_name = chunking_pipeline_name or (
                MARKDOWN_PIPELINE_NAME
                if content_type == "text/markdown"
                else PLAIN_TEXT_PIPELINE_NAME
            )
            pipeline = get_chunking_pipeline(pipeline_name)

            result = self._chunking_engine.chunk(
                document=ChunkDocument(
                    text=normalized_text,
                    content_type=content_type,
                    metadata=safe_metadata,
                ),
                pipeline=pipeline,
            )

            chunks = tuple(_to_tool_chunk(c) for c in result.chunks)

            index = ToolContentIndex(entries=tuple(
                ToolContentIndexEntry(
                    name=idx.name,
                    chunk_indices=idx.chunk_indices
                )
                for idx in result.indexes)
            )
            chunk_metadata = dict(result.metadata)
            used_chunking_pipeline_name = result.pipeline

        stored = StoredToolContent(
            content_id=f"cnt_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            producer=producer,
            source=source,
            content_type=content_type,
            content_role=role_value,
            text=normalized_text,
            chunks=chunks,
            index=index,
            metadata={
                **safe_metadata,
                "chunked": chunked,
                "chunking_pipeline": used_chunking_pipeline_name,
                "chunking": chunk_metadata,
            },
        )
        await self._repository.put(stored)
        return ToolContentReceipt(
            content_id=stored.content_id,
            content_type=stored.content_type,
            content_role=stored.content_role,
            original_length=len(stored.text),
            chunk_count=len(stored.chunks),
            read_modes=_read_modes(stored),
            selectors=_selectors(stored),
        )


    async def get(self, *, content_id: str, session_id: str) -> StoredToolContent | None:
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

def _to_tool_chunk(chunk: Chunk) -> ToolContentChunk:
    return ToolContentChunk(
        chunk_index=chunk.chunk_index,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        unit_types=tuple(str(v) for v in chunk.metadata.get("unit_types", ())),
        section_path=_first_section_path(chunk.metadata.get("section_paths")),
        anchor_names=tuple(str(v) for v in chunk.metadata.get("anchor_names", ())),
    )


def _first_section_path(value: object) -> tuple[str, ...]:
    """从 section_paths 取第一条路径；兼容嵌套 [["H1","H2"]] 和扁平 ["H1","H2"] 两种结构。"""
    if not isinstance(value, list | tuple) or not value:
        return ()
    first = value[0]
    items = first if isinstance(first, list | tuple) else value
    return tuple(str(item) for item in items)


def _read_modes(stored: StoredToolContent) -> tuple[str, ...]:
    if not stored.chunks:
        return ("continuous",)
    return "continuous", "ranked_expand", "regex_match"


def _selectors(stored: StoredToolContent) -> tuple[str, ...]:
    selectors: list[str] = []
    if any(chunk.unit_types for chunk in stored.chunks):
        selectors.append("unit_type")
    if stored.index is not None and stored.index.entries:
        selectors.extend(("section", "page", "anchor"))
    return tuple(selectors)
