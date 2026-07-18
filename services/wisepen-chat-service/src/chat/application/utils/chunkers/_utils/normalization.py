from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from ..models import Chunk, ChunkRole

DEFAULT_MIN_CHUNK_SIZE = 320

_HEADING_RE = re.compile(r"^#{1,6}\s+\S")


@dataclass(frozen=True, slots=True)
class ChunkMergeResult:
    """chunk 合并结果及被吸收 ID 到存活 ID 的直接映射。"""

    chunks: tuple[Chunk, ...]
    remapped_ids: dict[str, str]


def normalize_flat_chunks(chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
    """合并孤立标题和短尾，再生成最终 ID。"""
    heading_result = merge_heading_only(chunks)
    tail_result = merge_short_tails(
        heading_result.chunks,
        min_size=DEFAULT_MIN_CHUNK_SIZE,
    )
    return assign_chunk_ids(tail_result.chunks)


def normalize_parent_child_chunks(
    chunks: tuple[Chunk, ...],
) -> tuple[Chunk, ...]:
    """只归一化父块，并在最终定稿前维护所有子块引用。"""
    parents = tuple(chunk for chunk in chunks if chunk.role == ChunkRole.PARENT)
    children = tuple(chunk for chunk in chunks if chunk.role == ChunkRole.CHILD)

    heading_result = merge_heading_only(parents)
    tail_result = merge_short_tails(
        heading_result.chunks,
        min_size=DEFAULT_MIN_CHUNK_SIZE,
    )
    remapped_ids = _merge_remapped_ids(
        heading_result.remapped_ids,
        tail_result.remapped_ids,
    )
    # 标题合并和短尾合并可能连续吸收同一父块，先把两轮映射压平。
    children = tuple(
        replace(
            child,
            parent_chunk_id=remapped_ids.get(
                child.parent_chunk_id,
                child.parent_chunk_id,
            ),
        )
        for child in children
    )
    return assign_chunk_ids((*tail_result.chunks, *children))


def assign_chunk_ids(chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
    """根据最终文本重算 hash、ID 和连续索引，并定稿父子引用。"""
    id_map: dict[str, str] = {}
    finalized: list[Chunk] = []

    for index, chunk in enumerate(chunks):
        content_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        chunk_id = f"{chunk.role.value}:{index}:{content_hash[:16]}"
        id_map[chunk.chunk_id] = chunk_id
        finalized.append(
            replace(
                chunk,
                chunk_id=chunk_id,
                chunk_index=index,
                content_hash=content_hash,
            )
        )

    return tuple(
        replace(
            chunk,
            parent_chunk_id=id_map.get(chunk.parent_chunk_id, chunk.parent_chunk_id),
        )
        if chunk.parent_chunk_id is not None
        else chunk
        for chunk in finalized
    )


def merge_heading_only(chunks: tuple[Chunk, ...]) -> ChunkMergeResult:
    """将同页连续纯标题组并入相邻正文，同时记录所有被吸收 ID。"""
    if not chunks:
        return ChunkMergeResult(chunks, {})

    heading_only: list[bool] = []
    for chunk in chunks:
        lines = [line.strip() for line in chunk.text.splitlines() if line.strip()]
        heading_only.append(
            bool(lines) and all(_HEADING_RE.match(line) for line in lines)
        )

    merged: list[Chunk] = []
    remapped_ids: dict[str, str] = {}
    index = 0

    while index < len(chunks):
        if not heading_only[index]:
            merged.append(chunks[index])
            index += 1
            continue

        # 标题先按页分组，禁止 pending 状态把不同页的标题串在一起。
        heading_chunks = [chunks[index]]
        index += 1
        while (
            index < len(chunks)
            and heading_only[index]
            and _same_page(heading_chunks[0], chunks[index])
        ):
            heading_chunks.append(chunks[index])
            index += 1

        heading = heading_chunks[0]
        for part in heading_chunks[1:]:
            heading = _merge_pair(heading, part)

        # 同页后续正文优先承接标题，保持“标题在正文之前”的阅读顺序。
        if (
            index < len(chunks)
            and not heading_only[index]
            and _same_page(heading, chunks[index])
        ):
            body = chunks[index]
            index += 1
            for absorbed in (*heading_chunks[1:], body):
                remapped_ids[absorbed.chunk_id] = heading.chunk_id
            merged.append(_merge_pair(heading, body))
            continue

        # 没有后续正文时，末尾标题组只能并入同页前块。
        if merged and _same_page(merged[-1], heading):
            target_id = merged[-1].chunk_id
            for absorbed in heading_chunks:
                remapped_ids[absorbed.chunk_id] = target_id
            merged[-1] = _merge_pair(merged[-1], heading)
            continue

        for absorbed in heading_chunks[1:]:
            remapped_ids[absorbed.chunk_id] = heading.chunk_id
        merged.append(heading)

    return ChunkMergeResult(tuple(merged), remapped_ids)


def merge_short_tails(
    chunks: tuple[Chunk, ...],
    *,
    min_size: int,
) -> ChunkMergeResult:
    """把同页内小于阈值的短尾并入前块，绝不跨页。"""
    if len(chunks) <= 1:
        return ChunkMergeResult(chunks, {})

    merged: list[Chunk] = []
    remapped_ids: dict[str, str] = {}
    for chunk in chunks:
        if (
            not merged
            or len(chunk.text) >= min_size
            or not _same_page(merged[-1], chunk)
        ):
            merged.append(chunk)
            continue

        previous = merged[-1]
        remapped_ids[chunk.chunk_id] = previous.chunk_id
        merged[-1] = _merge_pair(previous, chunk)

    return ChunkMergeResult(tuple(merged), remapped_ids)


def _merge_pair(head: Chunk, body: Chunk) -> Chunk:
    """保留 head 身份并扩展文本、offset 与结构块范围。"""
    return replace(
        head,
        text=f"{head.text}\n\n{body.text}",
        end_offset=body.end_offset,
        end_block=body.end_block,
        content_hash="",
    )


def _same_page(left: Chunk, right: Chunk) -> bool:
    """无页码的普通文本属于同一范围；有页码时必须标签一致。"""
    left_page = left.metadata.get("page_label")
    right_page = right.metadata.get("page_label")
    if left_page is None and right_page is None:
        return True
    return left_page == right_page


def _merge_remapped_ids(
    first: dict[str, str],
    second: dict[str, str],
) -> dict[str, str]:
    """合并连续两轮 ID 映射，避免子块只跳转到中间父 ID。"""
    remapped = {
        old_id: second.get(target_id, target_id) for old_id, target_id in first.items()
    }
    remapped.update(second)
    return remapped
