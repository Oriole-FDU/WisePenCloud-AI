from __future__ import annotations

import hashlib
from dataclasses import replace

from ..core.models import Chunk


class ChunkFinalizer:
    """分块后处理终态器，按序执行三步修正：

    1. 纯标题合并 — 把只有 "Section: ..." 行的 chunk 并入相邻正文 chunk
       （MarkdownPreProcessor 注入的标题路径行，如果下方没有正文，就合并到前一个 chunk）
    2. 短尾合并 — 把过短的 chunk（< min_size）并入前一个 chunk
       （避免产生信息量不足的碎片 chunk）
    3. ID 生成 — 计算 content hash 并生成稳定的 chunk ID
       （格式：{prefix}:{level}:{index}:{hash前16位}，如 "doc:read:0:a1b2c3d4e5f6g7h8"）
    """

    __slots__ = ("name", "id_prefix", "min_size")

    def __init__(self, *, id_prefix: str = "", min_size: int = 320) -> None:
        self.name = "chunk_finalizer"
        self.id_prefix = id_prefix  # chunk ID 前缀，如文档 ID
        self.min_size = min_size  # 短尾合并阈值（字符数）

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        chunks = self._merge_heading_only(chunks)
        chunks = self._merge_short_tails(chunks)
        return self._assign_ids(chunks)

    # -- 1. 纯标题合并 -------------------------------------------------------
    #   场景：MarkdownPreProcessor 注入了 "Section: 快速开始 > 安装" 行，
    #   如果某个 chunk 只有这一行而没有正文，就把它合并到下一个有正文的 chunk 中；
    #   如果是最后一个 chunk，则合并到前一个 chunk 中。

    def _merge_heading_only(self, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        if not chunks:
            return chunks

        merged: list[Chunk] = []
        pending: Chunk | None = None  # 等待合并的纯标题 chunk

        for chunk in chunks:
            if _is_heading_only(chunk.text):
                # 纯标题 chunk：暂存，等待与下一个正文 chunk 合并
                pending = _merge_pair(pending, chunk) if pending else chunk
                continue
            if pending is not None:
                # 把暂存的纯标题 chunk 合并到当前正文 chunk 前面
                merged.append(_merge_pair(pending, chunk))
                pending = None
            else:
                merged.append(chunk)

        # 处理末尾剩余的纯标题 chunk
        if pending is not None:
            if merged:
                merged[-1] = _merge_pair(merged[-1], pending)
            else:
                merged.append(pending)

        return tuple(merged)

    # -- 2. 短尾合并 ---------------------------------------------------------
    #   场景：聚合后最后一个 chunk 可能只有几十字，信息量不足，
    #   将其拼接到前一个 chunk 末尾。

    def _merge_short_tails(self, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        if len(chunks) <= 1:
            return chunks

        merged: list[Chunk] = []
        for chunk in chunks:
            if merged and len(chunk.text) < self.min_size:
                # 短 chunk：拼接到前一个 chunk
                prev = merged[-1]
                merged[-1] = replace(
                    prev,
                    text=f"{prev.text}\n\n{chunk.text}",
                    end_offset=chunk.end_offset,
                    end_unit=chunk.end_unit,
                    content_hash="",
                )
            else:
                merged.append(chunk)

        return tuple(merged)

    # -- 3. ID 生成 ----------------------------------------------------------
    #   格式：{prefix}:{level}:{index}:{hash前16位}
    #   hash 保证相同内容产生相同 ID，支持幂等处理。

    def _assign_ids(self, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        result: list[Chunk] = []
        for chunk in chunks:
            content_hash = chunk.content_hash or hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            hash_suffix = content_hash[:16]
            parts: list[str] = []
            if self.id_prefix:
                parts.append(self.id_prefix)
            parts.append(chunk.level)
            parts.append(str(chunk.chunk_index))
            parts.append(hash_suffix)
            result.append(replace(chunk, chunk_id=":".join(parts), content_hash=content_hash))
        return tuple(result)


def _merge_pair(head: Chunk, body: Chunk) -> Chunk:
    """把 head 并入 body 前面，拼接文本并扩展 offset 范围。"""
    return replace(
        head,
        text=f"{head.text}\n{body.text}",
        end_offset=body.end_offset,
        end_unit=body.end_unit,
        content_hash="",
    )


def _is_heading_only(text: str) -> bool:
    """判断 chunk 是否只包含标题路径行（"Section: ..."）。

    MarkdownPreProcessor 会为标题下的正文注入 "Section: xxx > yyy" 前缀，
    如果某个 chunk 只有这种行而没有实际正文，就需要合并到相邻 chunk。
    """
    return all(
        line.startswith("Section: ")
        for line in text.splitlines()
        if line.strip()
    )
