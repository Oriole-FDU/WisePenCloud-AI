from __future__ import annotations

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndexEntry,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentSelector,
)


def select_chunks(
    stored: StoredToolContent,
    selector: ToolContentSelector | None,
) -> tuple[ToolContentChunk, ...]:
    chunks = tuple(sorted(stored.chunks, key=lambda c: c.chunk_index))
    if selector is None:
        return chunks

    selected: set[int] | None = None
    if selector.chunk_indices:
        selected = set(selector.chunk_indices)

    indexed = _select_indexed_chunks(stored, selector)
    if indexed is not None:
        selected = indexed if selected is None else selected & indexed

    if selector.block_kinds:
        block_kinds = set(selector.block_kinds)
        block_selected = {
            chunk.chunk_index
            for chunk in chunks
            if block_kinds & set(chunk.block_kinds)
        }
        selected = block_selected if selected is None else selected & block_selected

    if selected is None:
        selected = {chunk.chunk_index for chunk in chunks}

    return tuple(chunk for chunk in chunks if chunk.chunk_index in selected)


def _select_indexed_chunks(
    stored: StoredToolContent,
    selector: ToolContentSelector,
) -> set[int] | None:
    selected: set[int] | None = None

    for locator_kind, values in (
        ("section", selector.sections),
        ("page", selector.page_labels),
        ("anchor", selector.anchor_labels),
    ):
        if not values:
            continue

        matched: set[int] = set()
        for entry in stored.index.entries if stored.index else ():
            if entry.locator_kind == locator_kind and _matches_selector_value(entry, values):
                matched.update(entry.chunk_indices)

        selected = matched if selected is None else selected & matched

    return selected


def _matches_selector_value(
    entry: ToolContentIndexEntry,
    values: tuple[str, ...],
) -> bool:
    normalized_values = tuple(
        value.strip() for value in values if value and value.strip()
    )
    if entry.locator_kind == "page":
        locator_label = (
            entry.locator_name.removeprefix("page:")
            if entry.locator_name.startswith("page:")
            else entry.locator_name
        )
        # 页码必须按标签精确匹配，避免 page_labels=["4"] 误命中 page:14。
        page_values = (entry.page_label, locator_label)
        return any(
            target == candidate_text
            for target in normalized_values
            for candidate in page_values
            if (candidate_text := str(candidate or "").strip())
        )

    match_values = [entry.locator_name]
    if entry.locator_kind == "section":
        match_values.append(" > ".join(entry.section_path))
    elif entry.locator_kind == "anchor" and entry.anchor_label:
        match_values.append(entry.anchor_label)

    for target in normalized_values:
        for candidate in match_values:
            candidate_text = str(candidate).strip()
            if candidate_text and (
                target == candidate_text or target in candidate_text
            ):
                return True
    return False
