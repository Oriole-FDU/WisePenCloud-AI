from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from chat.application.algorithms.ranking.bm25 import rank_documents_by_bm25
from chat.application.algorithms.ranking.rrf import RRF_K, RankedList, weighted_rrf
from chat.application.algorithms.ranking.tokenizer import tokenize_for_bm25


@dataclass(frozen=True, slots=True)
class PlainTextDocument:
    """纯文本排序输入文档，不携带任何业务语义。"""

    document_id: str
    text: str
    original_rank: Optional[int] = None


@dataclass(frozen=True, slots=True)
class PlainTextRankRequest:
    """纯文本排序请求，支持单 query 或多 query 排序融合。

    Attributes:
        queries: 检索 query 列表。多 query 时会分别计算 BM25，再通过 RRF 融合。
        documents: 待排序的文档列表。
        top_k: 返回结果的最大数量。
        keyword_exact_ranked_ids: keyword exact 召回得到的额外排序信号。
        keyword_exact_queries: keyword exact 召回使用的查询文本，仅用于解释命中词。
        bm25_weight: BM25 排序信号的 RRF 权重。
        keyword_exact_weight: keyword exact 排序信号的 RRF 权重。
        original_rank_weight: original_rank 排序信号的 RRF 权重。
        include_original_rank: 是否将 original_rank 作为一路先验信号参与 RRF 融合。
    """

    queries: List[str]
    documents: List[PlainTextDocument]
    top_k: int
    keyword_exact_ranked_ids: Optional[List[str]] = None
    keyword_exact_queries: Optional[List[str]] = None
    bm25_weight: float = 1.0
    keyword_exact_weight: float = 1.2
    original_rank_weight: float = 0.4
    include_original_rank: bool = False


@dataclass(frozen=True, slots=True)
class PlainTextRankResult:
    """纯文本排序输出结果。

    Attributes:
        document_id: 文档 ID。
        rank: 排序位置（从 1 开始）。
        score: RRF 融合后的最终分数。
        matched_terms: 在所有 query 中命中该文档的检索词列表。
        exact_matched_terms: 在 keyword exact 查询中大小写不敏感命中的原始关键词列表。
        case_sensitive_exact_matched_terms: 在 keyword exact 查询中大小写敏感命中的原始关键词列表。
    """

    document_id: str
    rank: int
    score: float
    matched_terms: List[str]
    exact_matched_terms: List[str]
    case_sensitive_exact_matched_terms: List[str]


def rank_plain_text(request: PlainTextRankRequest) -> List[PlainTextRankResult]:
    """使用 BM25 与 RRF 对纯文本文档进行相关性排序。

    对每个 query 分别执行 BM25 检索，生成多路排序列表；
    多 query 时通过加权 RRF（Reciprocal Rank Fusion）融合各路排序；
    可选地将文档的原始排序位置作为额外一路先验信号参与融合。
    最终按融合分数降序排列，同分时按输入顺序稳定排序。

    Args:
        request: 排序请求，包含 queries、documents、top_k 等参数。

    Returns:
        按相关性降序排列的排序结果列表，每个结果包含文档 ID、排位、分数和命中的检索词。
    """

    # --- 1. query 归一化 ---
    normalized_queries: List[str] = []
    for query in request.queries:
        normalized_queries.append(query.strip())

    if not request.documents:
        return []

    # --- 2. 构建文档索引，记录原始输入顺序（用于稳定排序） ---
    documents: List[Tuple[str, str]] = []
    document_by_id: Dict[str, PlainTextDocument] = {}
    original_order: Dict[str, int] = {}
    for index, document in enumerate(request.documents):
        documents.append((document.document_id, document.text))
        document_by_id[document.document_id] = document
        original_order[document.document_id] = index

    # --- 3. 每路 query 单独执行 BM25，生成 RankedList ---
    ranked_lists: List[RankedList] = []
    for query_index, query in enumerate(normalized_queries):
        bm25_result = rank_documents_by_bm25(query, documents)
        ranked_lists.append(
            RankedList(
                name=f"bm25:{query_index}",
                ids=[item.id for item in bm25_result.ranked],
                weight=request.bm25_weight,
            )
        )

    # --- 4. 可选：将 keyword 作为额外一路先验信号 ---
    if request.keyword_exact_ranked_ids:
        ranked_lists.append(
            RankedList(
                name="keyword_exact",
                ids=request.keyword_exact_ranked_ids,
                weight=request.keyword_exact_weight,
            )
        )

    # --- 5. 可选：将 original_rank 作为额外一路先验信号 ---
    # original_rank 越小的文档（排位越靠前）在 RRF 中优先级越高
    if request.include_original_rank:
        original_ranked_ids = [
            document.document_id
            for document in sorted(
                request.documents,
                key=lambda item: (
                    item.original_rank is None,  # None 排到最后
                    item.original_rank if item.original_rank is not None else 0,
                    original_order[item.document_id],
                ),
            )
            if document.original_rank is not None
        ]
        if original_ranked_ids:
            ranked_lists.append(
                RankedList(
                    name="original_rank",
                    ids=original_ranked_ids,
                    weight=request.original_rank_weight,
                )
            )

    # --- 6. RRF 融合各路排序，得到最终分数 ---
    fused_items = weighted_rrf(ranked_lists, k=RRF_K)
    # 稳定排序：同分时保留原始输入顺序
    fused_items.sort(key=lambda item: (-item.score, original_order[item.id]))

    # --- 7. 收集所有 query 的去重检索词 ---
    query_terms: List[str] = []
    seen_terms: Set[str] = set()
    for query in normalized_queries:
        for term in tokenize_for_bm25(query):
            if term not in seen_terms:
                seen_terms.add(term)
                query_terms.append(term)

    exact_terms = request.keyword_exact_queries or []

    # --- 8. 构建最终结果，记录每个文档命中的检索词 ---
    exact_id_set = set(request.keyword_exact_ranked_ids or [])
    results: List[PlainTextRankResult] = []

    for rank, item in enumerate(fused_items[:request.top_k], 1):

        document = document_by_id[item.id]
        document_terms = set(tokenize_for_bm25(document.text))
        matched_terms = [term for term in query_terms if term in document_terms]
        lowered_text = document.text.casefold()

        exact_matched_terms = (
            [term for term in exact_terms if term.casefold() in lowered_text]
            if item.id in exact_id_set
            else []
        )
        case_sensitive_exact_matched_terms = (
            [term for term in exact_terms if term in document.text]
            if item.id in exact_id_set
            else []
        )

        results.append(
            PlainTextRankResult(
                document_id=item.id,
                rank=rank,
                score=item.score,
                matched_terms=matched_terms,
                exact_matched_terms=exact_matched_terms,
                case_sensitive_exact_matched_terms=case_sensitive_exact_matched_terms,
            )
        )

    return results
