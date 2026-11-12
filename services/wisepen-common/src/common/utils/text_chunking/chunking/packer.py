"""结构单元的装箱"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..model import Span, MarkdownNode

if TYPE_CHECKING:
    from .chunker import ChunkingPolicy, Section


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    """按结构顺序生成的 retrieval unit"""

    chunk_id: str # 稳定 ID chunk:<序号>:<文本哈希前16位>
    text: str # chunk 正文
    chunk_index: int # 全局顺序号（跨章节连续）
    node_ids: list[str] = field(default_factory=list) # 来源节点 ID，去重保序，记录这个 chunk 由哪些原始节点拼来
    section_path: list[str] = field(default_factory=list) # 所在章节路径（如 `["第一章","1.1"]`）
    content_token_count: int = 0 # 实际 token 数
    overflow: bool = False # 是否超硬阈值
    source_spans: list[Span] = field(default_factory=list) # 原文区间
    section_id: str | None = None # 所在章节 ID
    anchor_labels: list[str] = field(default_factory=list) # 涉及的编号题注标签
    content_hash: str = "" # 全文 SHA-256


class ChunkPacker:
    """按顺序组合同一章节的结构单元，构建最终 Chunk"""

    def __init__(self, policy: ChunkingPolicy) -> None:
        self._policy = policy

    def pack(
        self,
        nodes: Iterable[MarkdownNode],
        *,
        section: Section | None = None,
        start_index: int = 0,
    ) -> list[MarkdownChunk]:
        """将正文节点按顺序打包为 Chunk"""

        chunks: list[MarkdownChunk] = []
        selected = []  # 当前正在攒的 chunk 里的节点
        current_text = ""  # 当前 chunk 拼好的文本
        current_tokens = 0  # 当前 chunk 的 token 数

        def flush() -> None:
            """将当前已选节点封装为一个 Chunk，并清空暂存状态"""
            if selected:
                chunks.append(
                    self._build_chunk(
                        selected,
                        section=section,
                        chunk_index=start_index + len(chunks),
                    )
                )
                selected.clear()

        for node in nodes:
            node_tokens = self._policy.token_counter.count(node.text)

            # 节点自己就超硬阈值 → 强制独立成块
            if node_tokens > self._policy.split_threshold_tokens:
                flush()
                chunks.append(
                    self._build_chunk(
                        [node],
                        section=section,
                        chunk_index=start_index + len(chunks),
                    )
                )
                current_text, current_tokens = "", 0
                continue

            # 正常节点 → 尝试追加
            candidate = f"{current_text}\n{node.text}" if selected else node.text
            candidate_tokens = self._policy.token_counter.count(candidate)

            # 不允许追加 → flush() 封存当前 chunk，然后以这个节点重新开始
            if selected and not self._policy.should_append(
                current_tokens=current_tokens,
                candidate_tokens=candidate_tokens,
            ):
                flush()
                candidate, candidate_tokens = node.text, node_tokens

            selected.append(node)
            current_text, current_tokens = candidate, candidate_tokens

        # 处理末尾未封存的节点
        flush()
        return chunks

    def _build_chunk(
        self,
        nodes: Sequence[MarkdownNode],
        *,
        section: Section | None,
        chunk_index: int,
    ) -> MarkdownChunk:
        """根据节点序列构造最终 Chunk"""

        # text 和 content_token_count 都基于最终拼接结果算
        text = "\n".join(node.text for node in nodes)
        content_token_count = self._policy.token_counter.count(text)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        return MarkdownChunk(
            chunk_id=f"chunk:{chunk_index}:{content_hash[:16]}",
            chunk_index=chunk_index,
            text=text,
            # 去重并保留节点来源 ID 顺序。
            node_ids=list(
                dict.fromkeys(
                    identity for node in nodes for identity in node.source_node_ids
                )
            ),
            section_id=section.section_id if section else None,
            section_path=section.section_path if section else [],
            content_token_count=content_token_count,
            # 仅当实际 token 数超过阈值时标记溢出。
            overflow=content_token_count > self._policy.split_threshold_tokens,
            # 只保留有效 span，并去重。
            source_spans=list(
                dict.fromkeys(
                    span for node in nodes for span in node.source_spans if span.length
                )
            ),
            # 收集所有非空 anchor_label，并去重为字符串。
            anchor_labels=list(
                dict.fromkeys(
                    str(label)
                    for node in nodes
                    if (label := node.metadata.get("anchor_label")) is not None
                )
            ),
            content_hash=content_hash,
        )
