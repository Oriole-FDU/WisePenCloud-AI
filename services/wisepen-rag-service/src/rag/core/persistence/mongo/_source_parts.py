"""Mongo SourcePart 的分片、组装和连续性校验。"""

from collections.abc import Sequence
from dataclasses import dataclass

from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedResourceCorruptError,
)
from common.utils.document import SourceSpan

SOURCE_PART_CHARACTERS = 1_000_000


@dataclass(slots=True)
class SourcePart:
    """Mongo 中一个连续的权威 Markdown 存储分片。"""

    resource_id: str
    content_revision: str
    part_index: int
    source_span: SourceSpan
    text: str


def split_source_parts(
    *,
    resource_id: str,
    content_revision: str,
    markdown: str,
) -> list[SourcePart]:
    return [
        SourcePart(
            resource_id=resource_id,
            content_revision=content_revision,
            part_index=index,
            source_span=SourceSpan(
                start,
                min(start + SOURCE_PART_CHARACTERS, len(markdown)),
            ),
            text=markdown[start : start + SOURCE_PART_CHARACTERS],
        )
        for index, start in enumerate(range(0, len(markdown), SOURCE_PART_CHARACTERS))
    ]


def assemble_source_text(
    parts: Sequence[SourcePart],
    source_spans: Sequence[SourceSpan],
) -> str:
    """拼接 Python 字符 span，并拒绝缺失、重叠或损坏的存储分片。"""
    if not source_spans:
        return ""
    ordered_parts = sorted(parts, key=lambda part: part.source_span.start_offset)
    revision = ordered_parts[0].content_revision if ordered_parts else "unknown"
    _validate_parts(ordered_parts, revision)

    fragments: list[str] = []
    for span in source_spans:
        cursor = span.start_offset
        span_fragments: list[str] = []
        for part in ordered_parts:
            part_start = part.source_span.start_offset
            part_end = part.source_span.end_offset
            if part_end <= cursor:
                continue
            if part_start >= span.end_offset:
                break
            if part_start > cursor:
                raise PublishedResourceCorruptError(
                    f"content revision {revision} source parts have a gap"
                )
            fragment_end = min(part_end, span.end_offset)
            span_fragments.append(
                part.text[cursor - part_start : fragment_end - part_start]
            )
            cursor = fragment_end
            if cursor == span.end_offset:
                break
        if cursor != span.end_offset:
            raise PublishedResourceCorruptError(
                f"content revision {revision} source parts do not cover span"
            )
        fragments.append("".join(span_fragments))
    return "\n\n".join(fragments)


def _validate_parts(parts: Sequence[SourcePart], content_revision: str) -> None:
    previous_end = None
    for part in parts:
        start = part.source_span.start_offset
        end = part.source_span.end_offset
        if end - start != len(part.text):
            raise PublishedResourceCorruptError(
                f"content revision {content_revision} has an invalid source part"
            )
        if previous_end is not None and start < previous_end:
            raise PublishedResourceCorruptError(
                f"content revision {content_revision} source parts overlap"
            )
        previous_end = end
