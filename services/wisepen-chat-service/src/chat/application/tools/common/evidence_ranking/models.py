from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True, slots=True)
class EvidenceRankResult:
    """
    精排服务最终向网关/大模型交付的闭环答卷。
    """
    query: str
    evidence: List[RankedEvidence] = field(default_factory=list)
    total_chunks_scanned: int = 0
    content_ids_found: List[str] = field(default_factory=list)
    content_ids_missing: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RankedEvidence:
    """
    经过算法打分过滤后的黄金证据资产对象（切片级）。
    携带了该分块对应的物理分块索引、算法得分、溯源凭据以及命中高亮元数据。
    """
    content_id: str
    chunk_index: int
    score: float
    rank: int
    title: str = ""
    source: str = ""
    url: str = ""
    excerpt: str = ""
    start_offset: int = 0
    end_offset: int = 0
    source_id: str = ""
    domain: str = ""
    evidence_type: str = "chunk"
    matched_reason: str = ""
    term_hit_stats: Tuple[EvidenceTermHitStat, ...] = ()
    context_preview: Dict[str, object] = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        """
        高内聚的标题降级平铺解析器。
        按照 标题 -> 来源 -> URL -> 兜底默认值 的动态权重顺序，自动平滑回落，供大模型或前端高保真渲染。
        """
        return self.title or self.source or self.url or "(untitled)"


@dataclass(frozen=True, slots=True)
class EvidenceTermHitStat:
    """
    词项（Term）多维度命中统计聚合包。
    穿透反映单个 Query 关键词在所有不同文本字段中的物理分布特征。
    """
    term: str
    total_count: int
    field_stats: Tuple[EvidenceFieldHitStat, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceFieldHitStat:
    """
    单字段关键词命中统计指标。
    记录某个特定的物理元数据字段（如 title, body）中关键词出现的绝对频次。
    """
    field: str
    count: int
