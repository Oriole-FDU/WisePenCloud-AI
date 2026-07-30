from __future__ import annotations

from rag.core.persistence.mongo.rag_content_projection_repository import (
    _join_content_parts,
    _split_content,
)
from rag.domain.entities.rag_content import RagContentPartDocument


def test_content_parts_preserve_unicode_offsets() -> None:
    markdown = "甲" * 1_000_001

    parts = _split_content(markdown)
    documents = [
        RagContentPartDocument.model_construct(
            content_revision="revision-1",
            part_index=index,
            start_offset=start_offset,
            end_offset=end_offset,
            text=text,
        )
        for index, (start_offset, end_offset, text) in enumerate(parts)
    ]

    assert len(documents) == 2
    assert (documents[0].start_offset, documents[0].end_offset) == (0, 1_000_000)
    assert (documents[1].start_offset, documents[1].end_offset) == (
        1_000_000,
        1_000_001,
    )
    assert _join_content_parts(documents) == markdown
