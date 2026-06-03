import hashlib
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from rank_bm25 import BM25Okapi

from .tokenizer import tokenize_for_bm25

# 全局常量与线程安全缓存骨架
BM25_INDEX_CACHE_MAXSIZE = 32

BM25_INDEX_CACHE: OrderedDict[str, "CachedBm25Index"] = OrderedDict()
BM25_INDEX_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class Bm25RankedItem:
    """
    在特定检索通道中的打分与排名表现
    - id: 唯一标识符（如知识库中的 doc_id）
    - score: Bm25 算法计算出的原始得分
    - rank: 在当前通道结果集中的绝对排名（从 0 开始的索引）
    """
    id: str
    score: float
    rank: int

@dataclass(frozen=True, slots=True)
class Bm25RankResult:
    """
    BM25 传统关键词检索通道的完整返回包
    - ranked: 已排序的命中非变动文档元组，使用 Tuple 确保刚性只读
    - cache_hit: 缓存命中状态，True 表示直接走内存/Redis，未击中底层存储
    - build_index_elapsed_ms: 动态构建倒排索引的耗时（毫秒），常用于性能监控
    """
    ranked: Tuple[Bm25RankedItem, ...]
    cache_hit: bool = False
    build_index_elapsed_ms: int = 0


@dataclass(frozen=True, slots=True)
class CachedBm25Index:
    """
    内存中缓存的 BM25 预编译索引实体
    - doc_ids: 索引中包含的文档唯一标识符元组
    - bm25: rank_bm25 库的核心计算实例
    - fingerprint: 由文档内容生成的 SHA-256 数据指纹，用于校验数据是否过期
    """
    doc_ids: Tuple[str, ...]
    bm25: Optional[BM25Okapi]
    fingerprint: str


def rank_documents_by_bm25(
        query: str,
        documents: Sequence[Tuple[str, str]],
        cache_key: Optional[str] = None,
) -> Bm25RankResult:
    """
    利用 BM25 算法对输入的文档集进行关键词匹配计算与降序重排
    - query: 用户输入的原始查询文本
    - documents: 待检索的原始文档对队列，结构为 [(doc_id, text), ...]
    - cache_key: 缓存的唯一标识键（如 session_id），传入时开启 LRU 缓存
    """
    if not documents:
        return Bm25RankResult(ranked=())

    doc_ids = tuple(doc_id for doc_id, _ in documents)
    tokenized_query = tokenize_for_bm25(query)

    # 如果查询文本切分后为空，直接均分返回零分榜单
    if not tokenized_query:
        return Bm25RankResult(
            ranked=tuple(
                Bm25RankedItem(id=doc_id, score=0.0, rank=rank)
                for rank, doc_id in enumerate(doc_ids)
            )
        )

    # 如果只有一条文档，无需构建全量倒排索引，走单流捷径加速
    if len(documents) < 2:
        doc_id, text = documents[0]
        return Bm25RankResult(
            ranked=(
                Bm25RankedItem(
                    id=doc_id,
                    score=_score_single_document(text, tokenized_query),
                    rank=0,
                ),
            )
        )

    started = time.monotonic()
    cache_hit = False
    build_index_elapsed_ms = 0
    bm25: Optional[BM25Okapi] = None

    # 主循环：命中缓存或动态构建索引
    if cache_key:
        # 计算数据指纹
        digest = hashlib.sha256()
        for d_id, text in documents:
            digest.update(d_id.encode("utf-8", errors="ignore"))
            digest.update(b"\0")
            digest.update((text or "").encode("utf-8", errors="ignore"))
            digest.update(b"\0")
        fingerprint = digest.hexdigest()

        # 线程安全地从 LRU 中提取缓存
        with BM25_INDEX_CACHE_LOCK:
            cached = BM25_INDEX_CACHE.get(cache_key)
            if cached is not None and cached.fingerprint == fingerprint:
                bm25 = cached.bm25
                cache_hit = True
                BM25_INDEX_CACHE.move_to_end(cache_key)

        if not cache_hit:
            # 构建 BM25 倒排索引
            tokenized_docs = [tokenize_for_bm25(txt) for _, txt in documents]
            bm25 = BM25Okapi(tokenized_docs) if any(tokenized_docs) else None
            build_index_elapsed_ms = int((time.monotonic() - started) * 1000)

            # 线程安全地写入 LRU 缓存
            with BM25_INDEX_CACHE_LOCK:
                BM25_INDEX_CACHE[cache_key] = CachedBm25Index(
                    doc_ids=doc_ids, bm25=bm25, fingerprint=fingerprint
                )
                BM25_INDEX_CACHE.move_to_end(cache_key)
                while len(BM25_INDEX_CACHE) > BM25_INDEX_CACHE_MAXSIZE:
                    BM25_INDEX_CACHE.popitem(last=False)
    else:
        # 无缓存模式下，原地构建索引
        tokenized_docs = [tokenize_for_bm25(txt) for _, txt in documents]
        bm25 = BM25Okapi(tokenized_docs) if any(tokenized_docs) else None
        build_index_elapsed_ms = int((time.monotonic() - started) * 1000)

    # 批量算分与稳定排序
    if bm25 is None:
        ranked = tuple(
            Bm25RankedItem(id=doc_id, score=0.0, rank=rank)
            for rank, doc_id in enumerate(doc_ids)
        )
    else:
        scores = bm25.get_scores(tokenized_query)
        # 优先分数降序，分数持平按输入物理顺序升序
        ordered = sorted(
            enumerate(zip(doc_ids, scores)),
            key=lambda item: (-item[1][1], item[0]),
        )
        ranked = tuple(
            Bm25RankedItem(id=doc_id, score=float(score), rank=rank)
            for rank, (_, (doc_id, score)) in enumerate(ordered)
        )

    return Bm25RankResult(
        ranked=ranked,
        cache_hit=cache_hit,
        build_index_elapsed_ms=build_index_elapsed_ms,
    )


def _score_single_document(text: str, tokenized_query: List[str]) -> float:
    """
    单条文档检索时加速算分的降级算法
    - text: 待计算的单条文档正文
    - tokenized_query: 已分词的查询 Token 列表
    """
    tokenized_document = tokenize_for_bm25(text)
    if not tokenized_document:
        return 0.0

    query_terms = set(tokenized_query)
    document_terms = set(tokenized_document)
    overlap_terms = query_terms.intersection(document_terms)
    if not overlap_terms:
        return 0.0

    query_frequencies = Counter(tokenized_query)
    document_frequencies = Counter(tokenized_document)
    overlap_frequency = sum(
        min(query_frequencies[term], document_frequencies[term])
        for term in overlap_terms
    )
    return float(
        (2 * overlap_frequency) / (len(tokenized_query) + len(tokenized_document))
    )


