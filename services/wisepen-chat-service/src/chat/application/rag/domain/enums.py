from enum import StrEnum


class RetrievalChannel(StrEnum):
    """检索召回通道。

    三个通道分别对应三路召回：
    - dense_semantic: Qdrant dense vector semantic retrieval
    - sparse_lexical: Qdrant BM25 sparse lexical retrieval
    - keyword_exact: Elasticsearch keyword exact retrieval

    注意：sparse_lexical 可以辅助 exact mode，但不能替代 keyword_exact作为 exact mode 的命中判定依据。
    """

    # Qdrant dense vector，负责语义召回。
    DENSE_SEMANTIC = "dense_semantic"

    # Qdrant BM25 sparse vector，负责低成本词法召回。
    SPARSE_LEXICAL = "sparse_lexical"

    # Elasticsearch keyword exact，负责精确 token、phrase、identifier_terms 等召回。
    KEYWORD_EXACT = "keyword_exact"


class InsufficientReason(StrEnum):
    """检索不充分原因。

    - 该枚举只表达“检索层是否找到了足够相关候选”，不表达“证据是否足以完整回答用户问题”。
    - 最终回答充分性由 Agent / LLM 基于 RetrieveChunkEvidence 判断。
    """

    # 没有任何召回结果。
    NO_RESULTS = "no_results"

    # 有结果，但 top reranker score 低于当前 mode 的 LOW_SCORE 阈值。
    LOW_SCORE = "low_score"

    # exact mode 下没有 keyword_exact 命中。
    # sparse_lexical 命中不能替代 keyword_exact 命中。
    EXACT_MODE_NO_KEYWORD_HIT = "exact_mode_no_keyword_hit"


class NeighborRelation(StrEnum):
    """证据相邻上下文关系。

    - 用于 Neighbor Context Packing。
    - 表示 neighbor chunk 位于主 evidence chunk 的前面或后面。
    - 只描述父块之间的相对位置。
    - 不表达语义相关性。
    """

    # 主 evidence chunk 前一个相邻父块。
    BEFORE = "before"

    # 主 evidence chunk 后一个相邻父块。
    AFTER = "after"
