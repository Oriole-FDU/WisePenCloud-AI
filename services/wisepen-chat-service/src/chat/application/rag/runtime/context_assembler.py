from collections import defaultdict
from dataclasses import dataclass
from operator import attrgetter, itemgetter
from typing import List, Optional, Set

from chat.application.rag.runtime.retrieval.models import RagEvidence

MAX_CONTEXT_CHARS: int = 24000
BLOCK_SEPARATOR: str = "\n\n"
BLOCK_SEPARATOR_LEN: int = len(BLOCK_SEPARATOR)
GAP_PLACEHOLDER: str = "[...]"
GAP_PLACEHOLDER_LEN: int = len(GAP_PLACEHOLDER)


@dataclass(frozen=True, slots=True)
class RagAssembledContext:
    """RAG 组装后的上下文。"""
    text: str
    included_evidence_ids: List[str]
    skipped_evidence_count: int


class RagContextAssembler:
    """RAG Evidence 上下文组装器。"""

    def assemble(self, evidences: List[RagEvidence]) -> RagAssembledContext:
        if not evidences:
            return RagAssembledContext(text="", included_evidence_ids=[], skipped_evidence_count=0)

        # 将不同资源的知识碎片分组
        grouped = defaultdict(list)
        for ev in evidences:
            key = (ev.resource_kind.value, ev.resource_id)
            grouped[key].append(ev)

        # 组内、组间排序
        for group_items in grouped.values():
            group_items.sort(key=attrgetter("parent_chunk_index", "rank", "evidence_id"))
        sorted_groups = sorted(grouped.items(), key=itemgetter(0))

        blocks: List[str] = []
        included_evidence_ids: List[str] = []
        skipped_count = 0

        # 全局维护已用字符计步器
        # 代表当前已经成功并入全局干线的 blocks 如果用 "\n\n".join() 连起来的绝对物理长度
        total_chars = 0

        for group_key, group_evidences in sorted_groups:
            resource_kind, resource_id = group_key

            group_header = f"## Resource\n- resource_kind: {resource_kind}\n- resource_id: {resource_id}"
            group_blocks: List[str] = [group_header]
            group_included_ids: List[str] = []
            skipped_parent_chunk_ids: Set[str] = set()

            # 组内动态字数计步器
            group_chars = len(group_header)
            previous_parent_index: Optional[int] = None
            current_parent_chunk_id: Optional[str] = None

            for ev in group_evidences:
                # 状态指针流去重
                if ev.parent_chunk_id == current_parent_chunk_id:
                    continue
                current_parent_chunk_id = ev.parent_chunk_id

                # 产生单块文本
                evidence_block = self._build_evidence_block(ev)
                evidence_len = len(evidence_block)

                # 判断连续性
                has_gap = previous_parent_index is not None and ev.parent_chunk_index != previous_parent_index + 1

                # 新增内容净长度 = 块长度 + (如果存在断层，加上断层长和连字符长)
                added_net_chars = evidence_len
                if has_gap:
                    added_net_chars += BLOCK_SEPARATOR_LEN + GAP_PLACEHOLDER_LEN

                # 预估这一块加入后，当前 Resource 组的总长度
                # 连字符增量公式：列表每多追加 1 个元素，join 之后就必然且只多出 1 个 BLOCK_SEPARATOR_LEN
                projected_group_chars = group_chars + BLOCK_SEPARATOR_LEN + added_net_chars

                # 预估如果整个组并入大盘后，全局最终的总长度
                # 如果大盘当前是空的，全局长就是组长；如果大盘有内容，并入时会多产生一个全局连字符
                projected_total_chars = (
                    projected_group_chars
                    if total_chars == 0
                    else total_chars + BLOCK_SEPARATOR_LEN + projected_group_chars
                )

                # 拦截超限
                if projected_total_chars > MAX_CONTEXT_CHARS:
                    skipped_parent_chunk_ids.add(ev.parent_chunk_id)
                    continue

                # 安全过审，原地推进物理容器与状态
                if has_gap:
                    group_blocks.append(GAP_PLACEHOLDER)

                group_blocks.append(evidence_block)
                group_included_ids.append(ev.evidence_id)

                group_chars = projected_group_chars
                previous_parent_index = ev.parent_chunk_index

            skipped_count += len(skipped_parent_chunk_ids)

            # 过滤只有组头的空包
            if len(group_blocks) <= 1:
                continue

            # 加入全局主干干线
            blocks.extend(group_blocks)
            included_evidence_ids.extend(group_included_ids)

            # 全局物理计步器更新
            total_chars = (
                group_chars
                if total_chars == 0
                else total_chars + BLOCK_SEPARATOR_LEN + group_chars
            )

        return RagAssembledContext(
            text=BLOCK_SEPARATOR.join(blocks),
            included_evidence_ids=included_evidence_ids,
            skipped_evidence_count=skipped_count,
        )

    def _build_evidence_block(self, ev: RagEvidence) -> str:
        """文本块渲染"""
        lines = [
            f"### Evidence {ev.rank + 1}",
            f"- evidence_id: {ev.evidence_id}",
            f"- parent_chunk_id: {ev.parent_chunk_id}",
            f"- parent_chunk_index: {ev.parent_chunk_index}",
            f"- matched_channels: {', '.join(c.value for c in ev.matched_channels)}",
            f"- matched_queries: {', '.join(ev.matched_queries)}",
            f"- rerank_score: {ev.rerank_score:.4f}",
            f"- mmr_score: {ev.mmr_score:.4f}",
            f"- rrf_score: {ev.rrf_score:.4f}",
            "", "Retrieval context:", ev.retrieval_context,
            "", "Matched search text:", ev.search_text,
            "", "Parent chunk text:", ev.text,
        ]

        if ev.neighbor_contexts:
            lines.extend(["", "Neighbor texts:"])
            lines.extend(
                (
                    f"[{neighbor.relation.value} #{neighbor.chunk_index} "
                    f"{neighbor.chunk_id}] {neighbor.text}"
                )
                for neighbor in ev.neighbor_contexts
            )

        return "\n".join(lines)
