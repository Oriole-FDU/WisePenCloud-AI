from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass

from ..core.models import RankedCandidate
from ._diversify_utils import assign_ranks


@dataclass(frozen=True, slots=True)
class GroupRoundRobinDiversifierConfig:
    """基于 group key 的轻量轮询多样性控制配置。"""

    metadata_group_key: str | None = None
    max_per_group_per_round: int = 1
    preserve_unknown_group: bool = True
    unknown_group_prefix: str = "**unknown**"
    reason: str = "group_round_robin_diversified"


class GroupRoundRobinDiversifier:
    """按 group 首次出现顺序轮询候选，抑制单组连续霸榜。

    原理：先将候选按 group_key 分桶，然后多轮遍历所有组，
          每轮每组取 max_per_group_per_round 个，直至全部取完。
          相当于把「同一组的连续出现」摊开到整个序列中。
    """

    __slots__ = ("config", "name")

    def __init__(self, config: GroupRoundRobinDiversifierConfig | None = None) -> None:
        self.config = config or GroupRoundRobinDiversifierConfig()
        self.name = "group_round_robin_diversifier"

    def diversify(
        self,
        *,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:

        if not ranked:
            return ()

        # 1. 按 group 分桶，每个桶内保持原 ranked 顺序（不重新排序）
        #    OrderedDict 保留 group 首次出现的顺序，作为轮询顺序
        grouped: OrderedDict[str, deque[RankedCandidate]] = OrderedDict()
        groups_by_candidate_id: dict[str, str] = {}
        for item in ranked:
            group = self._group_for(item)
            grouped.setdefault(group, deque()).append(item)
            groups_by_candidate_id[item.candidate_id] = group

        # 2. 轮询：每轮遍历所有非空 group，每组取 max_per_group_per_round 个
        #    效果：将 A A A B C 摊成 A B C A A，降低同组连续霸榜
        per_round = max(self.config.max_per_group_per_round, 1)
        ordered: list[RankedCandidate] = []
        while grouped:
            empty_groups: list[str] = []
            for group, queue in grouped.items():
                for _ in range(per_round):
                    if not queue:
                        break
                    ordered.append(queue.popleft())
                if not queue:
                    empty_groups.append(group)

            for group in empty_groups:
                del grouped[group]

        # 3. 重排 rank 并附加多样化元数据（diversifier、原始排名、所属 group 等）
        return assign_ranks(
            tuple(ordered),
            reason_suffix="group_round_robin",
            metadata_by_candidate_id={
                item.candidate_id: {
                    "diversifier": self.name,
                    "original_rank": item.rank,
                    "original_score": item.score,
                    "diversity_group": groups_by_candidate_id[item.candidate_id],
                    "diversity_reason": self.config.reason,
                }
                for item in ordered
            },
        )

    def _group_for(self, item: RankedCandidate) -> str:
        """确定候选所属 group，优先级：group_key > metadata 字段 > 候选级未知。"""
        candidate = item.candidate
        if candidate.group_key:
            # 优先使用候选显式 group_key，比如 document_id/source/domain。
            return candidate.group_key

        metadata_group_key = self.config.metadata_group_key
        if metadata_group_key:
            value = candidate.metadata.get(metadata_group_key)
            if value is not None and str(value):
                # 没有 group_key 时，可退到 metadata 中指定字段。
                return str(value)

        if self.config.preserve_unknown_group:
            # 未知 group 默认每个候选单独成组，避免无 group 的候选互相挤压。
            return f"{self.config.unknown_group_prefix}:{candidate.candidate_id}"
        return self.config.unknown_group_prefix


