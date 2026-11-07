from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..parsing.parser import MarkdownNode, SourceSpan

if TYPE_CHECKING:
    from .chunker import ChunkingPolicy, Section


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    """按结构顺序生成的 retrieval unit。

    Chunker 不依赖 source_spans 做尺寸或合法性判断。Chat cache 可以在
    adapter 层消费它们，把 chunk 投影成自己的字符范围索引。
    """

    chunk_id: str
    text: str
    chunk_index: int
    node_ids: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    content_token_count: int = 0
    overflow: bool = False
    source_spans: list[SourceSpan] = field(default_factory=list)
    section_id: str | None = None
    anchor_labels: list[str] = field(default_factory=list)
    content_hash: str = ""


class ChunkPacker:
    """按顺序组合同一章节的结构单元，构建最终 Chunk 事实。"""

    def __init__(self, policy: ChunkingPolicy) -> None:
        """保存分块策略，供后续打包与阈值判断使用。"""
        self._policy = policy

    def pack(
        self,
        nodes: Iterable[MarkdownNode],
        *,
        section: Section | None = None,
        start_index: int = 0,
    ) -> list[MarkdownChunk]:
        """将正文节点按顺序打包为 Chunk；start_index 延续全局 Chunk 顺序。

        仅接收正文单元；若节点自身超过拆分阈值，则强制独立成块，
        避免因 BPE 非可加性导致与相邻节点混合后 token 数不可控。
        """
        chunks: list[MarkdownChunk] = []
        selected: list[MarkdownNode] = []
        current_text = ""
        current_tokens = 0

        def flush() -> None:
            """将当前已选节点封装为一个 Chunk，并清空暂存状态。"""
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

            # 不可拆单元必须独立输出，不能因 BPE 非可加性与邻居混合。
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

            # 尝试将当前节点追加到已有候选文本中。
            candidate = f"{current_text}\n{node.text}" if selected else node.text
            candidate_tokens = self._policy.token_counter.count(candidate)

            # 若策略不允许追加，则先封存当前 Chunk，再以该节点重新开始。
            if selected and not self._policy.should_append(
                current_tokens=current_tokens,
                candidate_tokens=candidate_tokens,
            ):
                flush()
                candidate, candidate_tokens = node.text, node_tokens

            selected.append(node)
            current_text, current_tokens = candidate, candidate_tokens

        # 处理末尾未封存的节点。
        flush()
        return chunks

    def _build_chunk(
        self,
        nodes: Sequence[MarkdownNode],
        *,
        section: Section | None,
        chunk_index: int,
    ) -> MarkdownChunk:
        """根据节点序列构造最终 Chunk。

        最终文本的 token 数与 Overflow 状态以实际拼接结果为准
        """
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
