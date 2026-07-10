from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from ..models import Chunk

_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+\S")


@dataclass(frozen=True, slots=True)
class ChunkMergeResult:
    """合并结果，携带 ID 重映射表。

    合并会导致被合并 chunk 的 chunk_id 消失（其文本拼接到存活 chunk 上）。
    remapped_ids 记录 "被合并的旧 chunk_id → 存活的旧 chunk_id"，
    供下游更新引用了被合并 chunk_id 的外键（如子 chunk 的 parent_chunk_id）。
    """

    chunks: tuple[Chunk, ...]
    remapped_ids: dict[str, str]


def assign_chunk_ids(chunks: tuple[Chunk, ...], *, id_prefix: str = "") -> tuple[Chunk, ...]:
    """为 chunks 生成最终 ID 和 content_hash，并更新子 chunk 的 parent_chunk_id。

    格式：{prefix}:{role}:{index}:{hash前16位}
    hash 保证相同内容产生相同 ID，支持幂等处理。

    嵌套分块场景下，子 chunk 的 parent_chunk_id 引用的是父 chunk 的旧 ID
    （block_packer 分配的临时 ID 或合并后存活的旧 ID），
    此函数基于 old_id → new_id 映射重写 parent_chunk_id，保证父子关系正确。
    """
    # 第一遍：生成新 ID 和 content_hash，建立 old_id → new_id 映射
    id_map: dict[str, str] = {}
    first_pass: list[Chunk] = []
    for chunk in chunks:
        content_hash = chunk.content_hash or hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        hash_suffix = content_hash[:16]
        parts: list[str] = []
        if id_prefix:
            parts.append(id_prefix)
        parts.append(chunk.role)
        parts.append(str(chunk.chunk_index))
        parts.append(hash_suffix)
        new_id = ":".join(parts)
        if chunk.chunk_id:
            id_map[chunk.chunk_id] = new_id
        first_pass.append(replace(chunk, chunk_id=new_id, content_hash=content_hash))

    # 第二遍：更新 parent_chunk_id（将旧 ID 替换为新 ID）
    if not id_map:
        return tuple(first_pass)
    return tuple(
        replace(chunk, parent_chunk_id=id_map.get(chunk.parent_chunk_id, chunk.parent_chunk_id))
        if chunk.parent_chunk_id is not None
        else chunk
        for chunk in first_pass
    )


def merge_heading_only(
    chunks: tuple[Chunk, ...],
    *,
    respect_page_boundaries: bool = True,
) -> ChunkMergeResult:
    """纯标题合并：把只有标题上下文的 chunk 并入相邻正文 chunk。

    返回 ChunkMergeResult，remapped_ids 记录被合并 chunk 的 ID 映射。
    """
    if not chunks:
        return ChunkMergeResult(chunks, {})

    merged: list[Chunk] = []
    remapped: dict[str, str] = {}
    pending: Chunk | None = None  # 等待合并的纯标题 chunk

    for chunk in chunks:
        lines = tuple(line.strip() for line in chunk.text.splitlines() if line.strip())
        if lines and all(_MARKDOWN_HEADING_RE.match(line) for line in lines):
            pending = _merge_pair(pending, chunk) if pending else chunk
            continue
        if pending is not None:
            # heading-only 合并到正文 chunk 前面，继承 pending 的 ID
            # 正文 chunk 的 ID 消失 → 映射到 pending 的 ID
            if _same_page_scope(
                pending,
                chunk,
                respect_page_boundaries=respect_page_boundaries,
            ):
                remapped[chunk.chunk_id] = pending.chunk_id
                merged.append(_merge_pair(pending, chunk))
            else:
                merged.append(pending)
                merged.append(chunk)
            pending = None
        else:
            merged.append(chunk)

    if pending is not None:
        if merged:
            # 末尾 heading-only 合并到前一个，继承前一个的 ID
            prev = merged[-1]
            if _same_page_scope(
                prev,
                pending,
                respect_page_boundaries=respect_page_boundaries,
            ):
                remapped[pending.chunk_id] = merged[-1].chunk_id
                merged[-1] = _merge_pair(prev, pending)
            else:
                merged.append(pending)
        else:
            merged.append(pending)

    return ChunkMergeResult(tuple(merged), remapped)


def merge_short_tails(
    chunks: tuple[Chunk, ...],
    *,
    min_size: int,
    respect_page_boundaries: bool = True,
) -> ChunkMergeResult:
    """短尾合并：把过短的 chunk（< min_size）并入前一个 chunk。

    返回 ChunkMergeResult，remapped_ids 记录被合并 chunk 的 ID 映射。
    """
    if len(chunks) <= 1:
        return ChunkMergeResult(chunks, {})

    merged: list[Chunk] = []
    remapped: dict[str, str] = {}

    for chunk in chunks:
        if not merged or len(chunk.text) >= min_size:
            merged.append(chunk)
            continue

        prev = merged[-1]
        if _same_page_scope(
            prev,
            chunk,
            respect_page_boundaries=respect_page_boundaries,
        ):
            # 短 chunk 合并到前一个，继承前一个的 ID
            remapped[chunk.chunk_id] = prev.chunk_id
            merged[-1] = _merge_pair(prev, chunk)
        else:
            merged.append(chunk)

    return ChunkMergeResult(tuple(merged), remapped)


def _merge_pair(head: Chunk, body: Chunk) -> Chunk:
    """保留 head 的 ID，将 body 拼到 head 后面并扩展 offset 范围。"""
    return replace(
        head,
        text=f"{head.text}\n\n{body.text}",
        end_offset=body.end_offset,
        end_block=body.end_block,
        content_hash="",
    )


def _same_page_scope(
    left: Chunk,
    right: Chunk,
    *,
    respect_page_boundaries: bool,
) -> bool:
    if not respect_page_boundaries:
        return True

    left_page_label = left.metadata.get("page_label")
    right_page_label = right.metadata.get("page_label")
    if left_page_label is None and right_page_label is None:
        return True
    return left_page_label == right_page_label
