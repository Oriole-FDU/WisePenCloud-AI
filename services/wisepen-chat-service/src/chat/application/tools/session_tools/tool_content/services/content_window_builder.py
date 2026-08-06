from __future__ import annotations

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.application.utils.chunkers import SourceSpan, TextLocator

from .models import ToolContentWindow


class ToolContentWindowBuilder:
    """从权威原文构建带字符坐标和字符预算保护的模型窗口。

    这个 builder 同时服务于两类输入：连续字符范围和由多个 source span
    组成的检索 chunk。前者可以直接返回一段连续原文，后者需要在片段之间
    插入分隔符并记录每段原文坐标，因此两条路径不能简单合并。
    """

    __slots__ = ("_char_budget",)

    def __init__(self, *, char_budget: int) -> None:
        """设置单个窗口的上限；调用方的共享总预算由 service 负责。"""

        if char_budget < 1:
            raise ValueError("char_budget must be greater than 0")
        self._char_budget = char_budget

    def build_range_window(
        self,
        stored: StoredToolContent,
        *,
        start: int | None,
        end: int | None,
        char_budget: int | None = None,
    ) -> ToolContentWindow:
        """读取一个连续字符范围，并在必要时从尾部截断。

        `start` 和 `end` 使用 Python 半开区间语义，允许负数并会被限制到
        原文范围内。字符预算只影响最终 `end`，不会改变原文坐标体系。
        """

        text_length = len(stored.text)
        normalized_start = _normalize_offset(start, text_length, default=0)
        requested_end = _normalize_offset(end, text_length, default=text_length)
        if requested_end <= normalized_start:
            normalized_end = normalized_start
            truncated = False
        else:
            # 先在请求范围内按字符预算截断，再把相对 offset 平移回完整原文坐标。
            budget = self._resolve_budget(char_budget)
            requested_length = requested_end - normalized_start
            included_chars = min(requested_length, budget)
            truncated = requested_length > budget
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
        char_budget: int | None = None,
    ) -> ToolContentWindow:
        """把 chunk 的多个原文片段拼成一个受预算保护的窗口。

        chunk 的 source spans 可能不连续；拼接时插入的两个换行属于返回文本，
        也会占用字符预算，但不属于任何一个原文 span。
        """

        budget = self._resolve_budget(char_budget)
        fragments: list[str] = []
        included_spans: list[SourceSpan] = []
        truncated = False
        for span_index, span in enumerate(chunk.source_spans):
            prefix = "\n\n".join(fragments)
            if prefix:
                prefix += "\n\n"
            available = budget - len(prefix)
            if available <= 0:
                # 前面的片段和分隔符已经用尽窗口预算，后面的 source span
                # 不能再返回；已收集的片段仍然是有效的部分结果。
                truncated = True
                break
            fragment = stored.text[span.start_offset : span.end_offset]
            included_chars = min(len(fragment), available)
            fragment_truncated = len(fragment) > available
            fragment = fragment[:included_chars]
            if not fragment and span.start_offset < span.end_offset:
                truncated = True
                break
            fragments.append(fragment)
            included_spans.append(
                SourceSpan(span.start_offset, span.start_offset + included_chars)
            )
            if fragment_truncated or span_index < len(chunk.source_spans) - 1 and (
                len("\n\n".join(fragments)) >= budget
            ):
                # 当前片段已被裁剪，或后面仍有未处理片段但预算已满。
                # 两种情况都意味着窗口不是 chunk 的完整展开。
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

    def _resolve_budget(self, char_budget: int | None) -> int:
        """把本次剩余预算限制在 builder 自身允许的窗口上限内。"""

        if char_budget is None:
            return self._char_budget
        # service 传入的是共享总预算的剩余值，不能让一次调用超过单窗口
        # 上限；至少保留一个字符，避免下层收到无效的零预算请求。
        return max(1, min(char_budget, self._char_budget))

    def _continuous_window(
        self,
        stored: StoredToolContent,
        *,
        start: int,
        end: int,
        truncated: bool,
    ) -> ToolContentWindow:
        """构建连续范围窗口，并附加与该范围相交的结构元数据。"""

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
    """提取 chunk 的原文片段，用于 ranking，不负责字符预算截断。"""

    return "\n\n".join(
        stored.text[span.start_offset : span.end_offset].strip()
        for span in chunk.source_spans
    )


def _normalize_offset(value: int | None, text_length: int, *, default: int) -> int:
    """将可选的 Python 字符 offset 归一化到 `[0, text_length]`。"""

    offset = default if value is None else value
    if offset < 0:
        # 负数遵循 Python slice 的相对末尾语义，再统一限制到合法边界。
        offset += text_length
    return min(max(offset, 0), text_length)


def _locator_labels(
    locators: tuple[TextLocator, ...],
    prefix: str,
) -> tuple[str, ...]:
    """提取指定 locator 前缀对应的去重标签，并保留首次出现顺序。"""

    return tuple(
        dict.fromkeys(
            locator.name.removeprefix(prefix)
            for locator in locators
            if locator.name.startswith(prefix)
        )
    )
