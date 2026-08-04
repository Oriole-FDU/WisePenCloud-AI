from __future__ import annotations

import hashlib
from dataclasses import replace

from ..models import Chunk


def assign_chunk_ids(chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
    """根据最终文本生成 hash、ID 和连续索引。"""
    finalized: list[Chunk] = []
    for index, chunk in enumerate(chunks):
        content_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        finalized.append(
            replace(
                chunk,
                chunk_id=f"chunk:{index}:{content_hash[:16]}",
                chunk_index=index,
                content_hash=content_hash,
            )
        )
    return tuple(finalized)
