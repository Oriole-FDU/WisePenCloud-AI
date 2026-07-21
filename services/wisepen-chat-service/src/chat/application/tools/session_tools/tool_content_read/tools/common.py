from __future__ import annotations

from typing import Any

from chat.application.utils.chunkers import BlockKind

CONTENT_IDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "maxItems": 64,
    "description": (
        "One or more cached cnt_* content IDs returned by earlier tool calls. "
        "Pass every cached document that may contain the answer; these are content IDs, not URLs."
    ),
}

SELECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Optional structural filter for limiting retrieval to known parts of the content. "
        "Values within one field are alternatives; different non-empty fields must all match. "
        "Omit this unless the relevant section, page, anchor, block kind, or chunk is already known."
    ),
    "properties": {
        "block_kinds": {
            "type": "array",
            "items": {"type": "string", "enum": [block_kind.value for block_kind in BlockKind]},
            "description": "Accept chunks containing any of these structural block kinds.",
        },
        "sections": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Accept chunks from sections whose name or section path contains any value.",
        },
        "page_labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Accept chunks from pages whose page label exactly equals any value.",
        },
        "anchor_labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Accept chunks whose anchor label contains any value.",
        },
        "chunk_indices": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Accept only these exact zero-based chunk indices.",
        },
    },
    "additionalProperties": False,
}
