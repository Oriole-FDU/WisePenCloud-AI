from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from enum import StrEnum

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

_DEFAULT_TOOL_CONTENT_MAX_CHARS = 20_000_000


class ToolContentPutStatus(StrEnum):
    STORED = "stored"
    EMPTY_TEXT = "empty_text"
    CONTENT_TOO_LARGE = "content_too_large"


@dataclass(frozen=True, slots=True)
class ToolContentPutResult:
    status: ToolContentPutStatus
    receipt: ToolContentReceipt | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ToolContentMetadataView:
    section_path: tuple[str, ...] = ()
    page_label: str | None = None
    anchor_label: str | None = None
    anchor_labels: tuple[str, ...] = ()

    @classmethod
    def from_metadata(cls, metadata: Metadata) -> "_ToolContentMetadataView":
        return cls(
            section_path=cls._str_tuple(metadata.get("section_path")),
            page_label=cls._str(metadata.get("page_label")),
            anchor_label=cls._str(metadata.get("anchor_label")),
            anchor_labels=cls._str_tuple(metadata.get("anchor_labels")),
        )

    @staticmethod
    def _str(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _str_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list | tuple):
            return ()
        values: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return ()
            values.append(item)
        return tuple(values)


class ToolContentStore:
    """工具内容存储门面：原始文本 → 分块 → 持久化 → receipt。"""

    __slots__ = ("_max_chars", "_repository")

    def __init__(
        self,
        *,
        repository: ToolContentRepository,
        max_chars: int = _DEFAULT_TOOL_CONTENT_MAX_CHARS,
    ) -> None:
        self._repository = repository
        self._max_chars = max(1, int(max_chars))

    async def put(
        self,
        *,
        session_id: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Metadata | None = None,
        chunking_engine_name: str | None = None,
        chunked: bool = True,
    ) -> ToolContentPutResult:
        """写入内容并返回 put 结果；空文本跳过，超长文本明确拒绝。"""
        normalized_text = text.strip()
        if not normalized_text:
            return ToolContentPutResult(
                status=ToolContentPutStatus.EMPTY_TEXT,
                reason="text is empty after stripping",
            )
        if len(normalized_text) > self._max_chars:
            return ToolContentPutResult(
                status=ToolContentPutStatus.CONTENT_TOO_LARGE,
                reason=f"text length {len(normalized_text)} exceeds max {self._max_chars}",
            )

        # content_hash 用于跨会话内容去重/一致性校验，必须基于分块前的原文计算
        safe_metadata: Metadata = {
            **(metadata or {}),
            "content_hash": hashlib.sha256(normalized_text.encode()).hexdigest(),
        }

        # chunked=False 时直接给出空壳数据，跳过分块引擎调用
        chunks, index, chunk_metadata, engine_name = (
            self._chunk(normalized_text, content_type, chunking_engine_name, safe_metadata)
            if chunked
            else ((), ToolContentIndex(), {}, None)
        )

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
                "chunking_engine": engine_name,
                "chunking": chunk_metadata,
            },
        )
        await self._repository.put(stored)
        return ToolContentPutResult(
            status=ToolContentPutStatus.STORED,
            receipt=ToolContentReceipt(
                content_id=stored.content_id,
                chunk_count=len(stored.chunks),
                supported_selectors=_selectors(stored),
            ),
        )

    def _chunk(
        self,
        text: str,
        content_type: str,
        engine_name: str | None,
        metadata: Metadata,
    ) -> tuple[tuple[ToolContentChunk, ...], ToolContentIndex, Metadata, str]:
        """执行分块并组装 chunks / index；从 put() 拆出，避免主流程被分块细节淹没。"""
        # 未显式指定引擎时按 content_type 走默认约定：markdown 走 markdown 引擎，其余走纯文本引擎
        engine = get_chunking_engine(
            engine_name or ("markdown" if content_type == "text/markdown" else "plain_text")
        )
        result = engine.chunk(
            document=ChunkDocument(text=text, content_type=content_type, metadata=metadata)
        )

        locator_view = _chunk_locator_view(result.locators)
        chunks = tuple(
            _to_tool_chunk(chunk, locator_view=locator_view.get(chunk.chunk_id))
            for chunk in result.chunks
        )
        index = ToolContentIndex(
            entries=tuple(_to_tool_index_entry(locator) for locator in result.locators)
        )
        return chunks, index, dict(result.metadata), result.pipeline

    async def get(
        self, *, content_id: str, session_id: str
    ) -> StoredToolContent | None:
        """按 content_id 读取；不存在或 session_id 不匹配返回 None。"""
        stored = await self._repository.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None
        return stored

# ---------------------------------------------------------------------------
# 模块级辅助函数
# ---------------------------------------------------------------------------


def _to_tool_chunk(
    chunk: Chunk,
    *,
    locator_view: dict[str, object] | None,
) -> ToolContentChunk:
    """将底层 Chunk 转为对外的 ToolContentChunk。"""
    locator_metadata = _ToolContentMetadataView.from_metadata(locator_view or {})

    return ToolContentChunk(
        chunk_index=chunk.chunk_index,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        block_kinds=_ToolContentMetadataView._str_tuple(
            chunk.metadata.get("block_kinds")
        ),
        section_path=locator_metadata.section_path,
        page_label=locator_metadata.page_label,
        anchor_labels=locator_metadata.anchor_labels,
    )


def _to_tool_index_entry(locator: ChunkLocator) -> ToolContentIndexEntry:
    metadata = _ToolContentMetadataView.from_metadata(locator.metadata)
    return ToolContentIndexEntry(
        locator_name=locator.name,
        locator_kind=locator.kind.value,
        chunk_indices=locator.chunk_indices,
        start_offset=locator.start_offset,
        end_offset=locator.end_offset,
        section_path=metadata.section_path,
        page_label=metadata.page_label,
        anchor_label=metadata.anchor_label,
    )


def _chunk_locator_view(
    locators: tuple[ChunkLocator, ...],
) -> dict[str, dict[str, object]]:
    """按 chunk_id 聚合三类 locator：section 取路径最深的一条，page 取首个命中，anchor 去重累加。"""
    chunk_view: dict[str, dict[str, object]] = {}

    for locator in locators:
        metadata = _ToolContentMetadataView.from_metadata(locator.metadata)
        if locator.kind == LocatorKind.SECTION:
            if not metadata.section_path:
                continue
            for chunk_id in locator.chunk_ids:
                entry = chunk_view.setdefault(chunk_id, {})
                current = entry.get("section_path")
                # 同一 chunk 可能命中多层 section locator，保留路径最深（信息量最大）的一条
                if (
                    not isinstance(current, tuple)
                    or len(metadata.section_path) > len(current)
                ):
                    entry["section_path"] = metadata.section_path

        elif locator.kind == LocatorKind.PAGE:
            if metadata.page_label:
                for chunk_id in locator.chunk_ids:
                    chunk_view.setdefault(chunk_id, {}).setdefault(
                        "page_label",
                        metadata.page_label,
                    )

        elif locator.kind == LocatorKind.ANCHOR:
            if not metadata.anchor_label:
                continue
            for chunk_id in locator.chunk_ids:
                labels = chunk_view.setdefault(chunk_id, {}).setdefault("anchor_labels", [])
                if metadata.anchor_label not in labels:
                    labels.append(metadata.anchor_label)

    # anchor_labels 在聚合期间用 list（需要去重判断），出参前统一冻结为 tuple
    for values in chunk_view.values():
        if isinstance(values.get("anchor_labels"), list):
            values["anchor_labels"] = tuple(values["anchor_labels"])

    return chunk_view


def _selectors(stored: StoredToolContent) -> tuple[str, ...]:
    """汇总该内容支持的检索维度，写入 receipt 告知调用方可以用哪些方式定位 chunk。"""
    selectors: list[str] = []
    if stored.chunks:
        selectors.append("chunk_indices")
    if any(chunk.block_kinds for chunk in stored.chunks):
        selectors.append("block_kind")
    if stored.index is not None and stored.index.entries:
        selectors.extend(("section", "page_label", "anchor_label"))
    return tuple(selectors)
