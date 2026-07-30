from __future__ import annotations

from typing import Any

from common.utils.chunkers import BlockKind

CONTENT_IDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "maxItems": 64,
    "description": (
        "Required. One or more cnt_* ids from previous content_receipts. "
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
            "items": {"type": "string", "enum": [block_kind.value for block_kind in BlockKind]},
            "description": "Restrict search to chunks carrying these structural block kinds.",
        },
        "sections": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Restrict search to matching section names or path fragments.",
        },
        "page_labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Restrict search to exact page labels.",
        },
        "anchor_labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Restrict search to matching anchor labels.",
        },
        "chunk_indices": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Restrict search to explicit chunk indices.",
        },
    },
    "additionalProperties": False,
}
