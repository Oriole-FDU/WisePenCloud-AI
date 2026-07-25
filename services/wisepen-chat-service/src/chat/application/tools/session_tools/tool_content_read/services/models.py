from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolContentSelector:
    """读取前置候选域过滤器，多个条件按交集生效。"""

    block_kinds: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    page_labels: tuple[str, ...] = ()
    anchor_labels: tuple[str, ...] = ()
    chunk_indices: tuple[int, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ToolContentSelector:
        payload = payload or {}
        return cls(
            block_kinds=tuple(payload.get("block_kinds", ())),
            sections=tuple(payload.get("sections", ())),
            page_labels=tuple(payload.get("page_labels", ())),
            anchor_labels=tuple(payload.get("anchor_labels", ())),
            chunk_indices=tuple(payload.get("chunk_indices", ())),
        )


@dataclass(frozen=True, slots=True)
class ToolContentRankedExpandReadRequest:
    content_ids: tuple[str, ...]
    query: str
    selector: ToolContentSelector | None = None
    top_k: int = 10
    merge_before: int = 0
    merge_after: int = 0


@dataclass(frozen=True, slots=True)
class ToolContentRegexReadRequest:
    content_ids: tuple[str, ...]
    pattern: str
    selector: ToolContentSelector | None = None
    max_matches: int = 10
    merge_before: int = 0
    merge_after: int = 0


@dataclass(frozen=True, slots=True)
class ToolContentWindow:
    """一次读取产生的模型上下文窗口及其原文定位信息。"""

    text: str
    start_offset: int | None = None
    end_offset: int | None = None
    center_chunk: int | None = None
    chunk_start: int | None = None
    chunk_end: int | None = None
    page_labels: tuple[str, ...] = ()
    section_paths: tuple[tuple[str, ...], ...] = ()
    anchor_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentReadFailure:
    content_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ToolContentRegexMatch:
    content_id: str
    window: ToolContentWindow


@dataclass(frozen=True, slots=True)
class ToolContentRegexReadResult:
    matches: tuple[ToolContentRegexMatch, ...] = ()
    failed: tuple[ToolContentReadFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentRankedExpandItem:
    content_id: str
    rank: int
    score: float
    window: ToolContentWindow


@dataclass(frozen=True, slots=True)
class ToolContentRankedExpandReadResult:
    ranked: tuple[ToolContentRankedExpandItem, ...] = ()
    failed: tuple[ToolContentReadFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentReadResult:
    content_id: str
    window: ToolContentWindow | None = None
    reason: str | None = None
