from __future__ import annotations

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndexEntry,
)

from ...models import ToolContentSelector


def select_chunks(
        stored: StoredToolContent,
        selector: ToolContentSelector | None,
) -> tuple[ToolContentChunk, ...]:
    chunks = tuple(sorted(stored.chunks, key=lambda chunk: chunk.chunk_index))
    if selector is None:
        return chunks

    selected = set(selector.chunk_indices) if selector.chunk_indices else None
    indexed = _select_indexed_chunks(stored, selector)
    if indexed is not None:
        selected = indexed if selected is None else selected & indexed

    if selector.block_kinds:
        block_kinds = set(selector.block_kinds)
        matched = {
            chunk.chunk_index
            for chunk in chunks
            if block_kinds & set(chunk.block_kinds)
        }
        selected = matched if selected is None else selected & matched

    if selected is None:
        return chunks
    return tuple(chunk for chunk in chunks if chunk.chunk_index in selected)


def _select_indexed_chunks(
        stored: StoredToolContent,
        selector: ToolContentSelector,
) -> set[int] | None:
    selected: set[int] | None = None
    for kind, values in (
            ("section", selector.sections),
            ("page", selector.page_labels),
            ("anchor", selector.anchor_labels),
    ):
        if not values:
            continue

        matched: set[int] = set()
        for entry in stored.index.entries if stored.index else ():
            if entry.locator_kind == kind and _matches(entry, values):
                matched.update(entry.chunk_indices)
        selected = matched if selected is None else selected & matched

    return selected


def _matches(entry: ToolContentIndexEntry, values: tuple[str, ...]) -> bool:
    targets = tuple(value.strip() for value in values if value.strip())
    if entry.locator_kind == "page":
        locator_label = (
            entry.locator_name.removeprefix("page:")
            if entry.locator_name.startswith("page:")
            else entry.locator_name
        )
        return any(
            target == candidate_text
            for target in targets
            for candidate in (entry.page_label, locator_label)
            if (candidate_text := str(candidate or "").strip())
        )

    candidates = [entry.locator_name]
    if entry.locator_kind == "section":
        candidates.append(" > ".join(entry.section_path))
    elif entry.anchor_label:
        candidates.append(entry.anchor_label)

    for target in targets:
        for candidate in candidates:
            candidate_text = str(candidate).strip()
            if candidate_text and (
                    target == candidate_text or target in candidate_text
            ):
                return True
    return False
