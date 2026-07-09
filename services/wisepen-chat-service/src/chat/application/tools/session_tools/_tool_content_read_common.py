from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable, TypeVar

from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentReadMatch,
    ToolContentReadResult,
    ToolContentRegexReadRequest,
    ToolContentRerankReadRequest,
    ToolContentSelector,
)
from chat.application.tools.utils.batching import batched
from chat.application.utils.chunking_engine import BlockKind


BLOCK_KIND_ENUM = [block_kind.value for block_kind in BlockKind]
ToolContentBatchRequest = TypeVar(
    "ToolContentBatchRequest",
    ToolContentRerankReadRequest,
    ToolContentRegexReadRequest,
)
CONTENT_IDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "string",
        "minLength": 1,
    },
    "minItems": 1,
    "maxItems": 64,
    "description": (
        "Required. One or more cnt_* ids from previous <content_receipt> values. "
        "Multiple ids are split into bounded internal read batches."
    ),
}
SELECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Optional chunk prefilter applied before reading. "
        "Multiple selector groups are intersected."
    ),
    "properties": {
        "block_kinds": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": BLOCK_KIND_ENUM,
                "description": "One structural block kind value from the chunking engine BlockKind enum.",
            },
            "description": "Optional. Restrict search to chunks carrying these structural block kinds.",
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
                "description": "One section name or section path fragment.",
            },
            "description": "Optional. Restrict search to matching section names or section path fragments.",
        },
        "page_labels": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
                "description": "One page label such as '3'.",
            },
            "description": "Optional. Restrict search to matching page labels when page metadata exists.",
        },
        "anchor_labels": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
                "description": "One anchor label such as a table, figure, or equation identifier.",
            },
            "description": "Optional. Restrict search to matching anchor labels.",
        },
        "chunk_indices": {
            "type": "array",
            "items": {
                "type": "integer",
                "description": "One chunk index within a content_id.",
            },
            "description": "Optional prefilter to restrict search to known chunk indices.",
        },
    },
    "additionalProperties": False,
}


def selector_from_payload(payload: dict[str, Any] | None) -> ToolContentSelector:
    payload = payload or {}
    return ToolContentSelector(
        block_kinds=_read_str_tuple(payload.get("block_kinds")),
        sections=_read_str_tuple(payload.get("sections")),
        page_labels=_read_str_tuple(payload.get("page_labels")),
        anchor_labels=_read_str_tuple(payload.get("anchor_labels")),
        chunk_indices=_read_int_tuple(payload.get("chunk_indices")),
    )


def _read_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    )


def _read_int_tuple(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(
        item
        for item in value
        if isinstance(item, int) and not isinstance(item, bool)
    )


async def read_content_id_batches(
    *,
    request: ToolContentBatchRequest,
    batch_size: int,
    read_batch: Callable[[ToolContentBatchRequest], Awaitable[ToolContentReadResult]],
) -> ToolContentReadResult:
    matches = []
    failed = []
    for batch_content_ids in batched(
        request.content_ids,
        batch_size=int(batch_size),
    ):
        try:
            batch_result = await read_batch(replace(request, content_ids=batch_content_ids))
        except Exception as exc:
            # 容灾机制，防止超时时结果被全部截断，会返回已成功读取结果
            failed.extend(
                ToolContentReadMatch(
                    content_id=content_id,
                    reason=exc.__class__.__name__,
                )
                for content_id in batch_content_ids
            )
            continue
        matches.extend(batch_result.matches)
        failed.extend(batch_result.failed)

    return ToolContentReadResult(
        matches=tuple(matches),
        failed=tuple(failed),
    )
