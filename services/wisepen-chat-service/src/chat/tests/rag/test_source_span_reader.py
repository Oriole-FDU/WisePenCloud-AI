from __future__ import annotations

from chat.core.persistence.mongo.rag_content_projection_repository import (
    _read_source_spans,
)
from chat.domain.entities.rag_content import (
    RagContentPartDocument,
    RagSourceSpanDocument,
)


def test_reads_source_span_across_content_parts() -> None:
    documents = [
        RagContentPartDocument.model_construct(
            content_revision="revision-1",
            part_index=0,
            start_offset=0,
            end_offset=1_000_000,
            text="a" * 1_000_000,
        ),
        RagContentPartDocument.model_construct(
            content_revision="revision-1",
            part_index=1,
            start_offset=1_000_000,
            end_offset=1_000_002,
            text="bc",
        ),
    ]

    content = _read_source_spans(
        documents,
        [RagSourceSpanDocument(start_offset=999_999, end_offset=1_000_002)],
    )

    assert content == "abc"
