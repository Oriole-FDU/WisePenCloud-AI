from __future__ import annotations

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.application.tools.common.canonical_token_budget import (
    count_canonical_tokens,
    truncate_canonical_prefix,
)
from chat.application.utils.chunkers import SourceSpan, TextLocator

from .models import ToolContentWindow

class ToolContentWindowBuilder:
    """从权威原文构建有模型可见 token 预算保护的窗口。"""

    __slots__ = ("_token_budget",)

    def __init__(self, *, token_budget: int) -> None:
        if token_budget < 1:
            raise ValueError("token_budget must be greater than 0")
        self._token_budget = token_budget

    def build_range_window(
        self,
        stored: StoredToolContent,
        *,
        start: int | None,
        end: int | None,
        token_budget: int | None = None,
    ) -> ToolContentWindow:
        text_length = len(stored.text)
        normalized_start = _normalize_offset(start, text_length, default=0)
        requested_end = _normalize_offset(end, text_length, default=text_length)
        if requested_end <= normalized_start:
            normalized_end = normalized_start
            truncated = False
        else:
            _, included_chars, truncated = truncate_canonical_prefix(
                stored.text[normalized_start:requested_end],
                self._resolve_budget(token_budget),
            )
            normalized_end = normalized_start + included_chars
        return self._continuous_window(
            stored,
            start=normalized_start,
            end=normalized_end,
            truncated=truncated,
        )

    def build_source_window(
        self,
        stored: StoredToolContent,
        *,
        chunk: ToolContentChunk,
        token_budget: int | None = None,
    ) -> ToolContentWindow:
        budget = self._resolve_budget(token_budget)
        fragments: list[str] = []
        included_spans: list[SourceSpan] = []
        truncated = False
        for span_index, span in enumerate(chunk.source_spans):
            prefix = "\n\n".join(fragments)
            if prefix:
                prefix += "\n\n"
            available = budget - count_canonical_tokens(prefix)
            if available <= 0:
                truncated = True
                break
            fragment = stored.text[span.start_offset : span.end_offset]
            fragment, included_chars, fragment_truncated = truncate_canonical_prefix(
                fragment,
                available,
            )
            while fragment and count_canonical_tokens(prefix + fragment) > budget:
                available -= 1
                fragment, included_chars, fragment_truncated = truncate_canonical_prefix(
                    stored.text[span.start_offset : span.end_offset],
                    available,
                )
            if not fragment and span.start_offset < span.end_offset:
                truncated = True
                break
            fragments.append(fragment)
            included_spans.append(
                SourceSpan(span.start_offset, span.start_offset + included_chars)
            )
            if fragment_truncated or span_index < len(chunk.source_spans) - 1 and (
                count_canonical_tokens("\n\n".join(fragments)) >= budget
            ):
                truncated = True
                break

        start = min((span.start_offset for span in included_spans), default=0)
        end = max((span.end_offset for span in included_spans), default=0)
        return ToolContentWindow(
            text="\n\n".join(fragments),
            start_offset=start,
            end_offset=end,
            source_spans=tuple(included_spans),
            page_labels=chunk.page_labels,
            section_paths=tuple(" > ".join(path) for path in chunk.section_paths),
            anchor_labels=chunk.anchor_labels,
            truncated=truncated,
            metadata=dict(stored.metadata),
        )

    def _resolve_budget(self, token_budget: int | None) -> int:
        if token_budget is None:
            return self._token_budget
        return max(1, min(token_budget, self._token_budget))

    def _continuous_window(
        self,
        stored: StoredToolContent,
        *,
        start: int,
        end: int,
        truncated: bool,
    ) -> ToolContentWindow:
        locators = tuple(
            locator
            for locator in stored.locators
            if locator.start_offset < end and locator.end_offset > start
        )
        return ToolContentWindow(
            text=stored.text[start:end],
            start_offset=start,
            end_offset=end,
            source_spans=(SourceSpan(start, end),) if start < end else (),
            page_labels=_locator_labels(locators, "page:"),
            section_paths=tuple(
                locator.name.removeprefix("section:")
                for locator in locators
                if locator.name.startswith("section:")
            ),
            anchor_labels=_locator_labels(locators, "anchor:"),
            truncated=truncated,
            metadata=dict(stored.metadata),
        )


def chunk_text(stored: StoredToolContent, chunk: ToolContentChunk) -> str:
    return "\n\n".join(
        stored.text[span.start_offset : span.end_offset].strip()
        for span in chunk.source_spans
    )


def _normalize_offset(value: int | None, text_length: int, *, default: int) -> int:
    offset = default if value is None else value
    if offset < 0:
        offset += text_length
    return min(max(offset, 0), text_length)


def _locator_labels(
    locators: tuple[TextLocator, ...],
    prefix: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            locator.name.removeprefix(prefix)
            for locator in locators
            if locator.name.startswith(prefix)
        )
    )
