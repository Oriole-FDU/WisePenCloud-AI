from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence

from .bm25 import rank_documents_by_bm25

FIELDED_BM25_CACHE_PREFIX = "fielded_bm25"


@dataclass(frozen=True, slots=True)
class FieldedItem:
    """带有多字段约束的原始文档结构（常用于多字段 BM25 检索）

    - id: 文档的唯一标识符
    - fields: 多字段 KV 映射，例如：{"title": "服务重启指南", "content": "内容..."}
    """
    id: str
    fields: Dict[str, str]


def score_fielded_bm25(
        query: str,
        items: Sequence[FieldedItem],
        field_weights: Mapping[str, float],
) -> Dict[str, float]:
    """
    对多字段文档集进行加权 BM25 算分，返回文档 ID 与总分的映射字典
    - query: 用户输入的原始查询文本
    - items: 包含多字段信息的原始文档序列
    - field_weights: 字段名与权重的映射关系，例如：{"title": 1.0, "content": 0.4}
    """
    scores = {document.id: 0.0 for document in items}
    if not items:
        return scores

    # 遍历每个配置的字段，分路计算 BM25 评分并加权累加
    for field_name, weight in field_weights.items():
        field_items = [
            (document.id, document.fields.get(field_name, "") or "")
            for document in items
        ]

        # rank_documents_by_bm25 内部自动计算指纹并防击穿
        cache_key = f"{FIELDED_BM25_CACHE_PREFIX}:{field_name}"

        result = rank_documents_by_bm25(
            query,
            field_items,
            cache_key=cache_key,
        )

        # 累加当前字段的加权得分
        for item in result.ranked:
            scores[item.id] = scores.get(item.id, 0.0) + (float(weight) * item.score)

    return scores


def rank_fielded_bm25(
        query: str,
        items: Sequence[FieldedItem],
        field_weights: Mapping[str, float],
) -> List[str]:
    """
    结合多字段加权得分，对文档集进行降序重排，仅返回排序后的文档 ID 列表
    - query: 用户输入的原始查询文本
    - items: 包含多字段信息的原始文档序列
    - field_weights: 字段名与权重的映射关系
    """
    scores = score_fielded_bm25(query, items, field_weights)

    # 优先总分降序；若分数持平，严格按文档传入的物理先后顺序升序排（稳定排序）
    ordered = sorted(
        enumerate(items),
        key=lambda item: (-scores.get(item[1].id, 0.0), item[0]),
    )
    return [item.id for _, item in ordered]