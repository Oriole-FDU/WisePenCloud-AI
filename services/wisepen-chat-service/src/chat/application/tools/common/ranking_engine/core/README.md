# Ranking Engine Core 机制说明

这套 Ranking Engine 的核心目标是：把“怎么排序”从具体工具里拆出来，做成一套可插拔流水线。

它只关心排序，不关心工具、不关心 Redis、不关心 session、不关心 content_id 权限。外部系统先把数据整理成标准候选对象，Ranking Engine 只负责把候选排好。

## 一句话理解

当前排序链路是：

```text
RankCandidate
  -> Scorer
  -> ScoreSignal
  -> Fusion
  -> RankedCandidate
  -> Reranker
  -> Diversifier
  -> RankResult
```

换成人话：

1. 外部传进来一批候选 `RankCandidate`。
2. 多个 `Scorer` 分别给候选打分，产出一堆 `ScoreSignal`。
3. `Fusion` 把这些信号融合成第一版排序结果 `RankedCandidate`。
4. `Reranker` 可以基于第一版结果做二次精排。
5. `Diversifier` 可以做多样性控制，避免同来源、同文档、同 group 霸榜。
6. `RankingEngine` 统一补连续 rank，裁剪 top_k，返回 `RankResult`。

## 核心文件

```text
models.py      # 数据对象
protocols.py   # 插件协议
pipeline.py    # 流水线定义
engine.py      # 编排执行器
```

## 数据对象

### RankQuery

一次排序的查询对象。

```python
RankQuery(
    text="主查询",
    queries=("扩展查询1", "扩展查询2"),
)
```

`text` 是主查询，`queries` 是扩展查询。`all_queries` 会返回去重后的全部非空查询，并且主查询排在最前。

适合场景：

- 用户原始 query 放 `text`
- query rewrite、同义扩展、多语言扩展放 `queries`

### RankCandidate

排序候选。

Ranking Engine 只认识这种候选，不认识业务对象。

```python
RankCandidate(
    candidate_id="doc-1",
    text="候选全文",
    fields={
        "title": "标题",
        "body": "正文",
        "heading": "章节路径",
    },
    prior_rank=3,
    group_key="file-abc",
)
```

字段含义：

- `candidate_id`：候选唯一 ID。
- `text`：主文本，给全文 BM25、向量、模型重排用。
- `fields`：字段化文本，给 fielded BM25 用。
- `prior_rank`：外部已有排序，可以作为先验信号。
- `group_key`：多样性控制分组，例如同文件、同 URL、同父 chunk。
- `metadata`：业务附加信息，Ranking Engine 不解释。

注意：外部业务对象不要直接进 engine。比如 `StoredContent`、`ContentChunk`、搜索结果对象，都应该先转换成 `RankCandidate`。

### ScoreSignal

单个插件产出的排序信号。

例如：

```python
ScoreSignal(
    candidate_id="doc-1",
    name="bm25:title",
    value=12.5,
    kind=ScoreSignalKind.FIELD,
    rank=1,
    weight=3.0,
    reason="Matched query terms in title.",
)
```

它表达的是：“某个 scorer 认为某个候选有多少分”。

常见信号：

- `bm25:text`
- `bm25:title`
- `keyword_exact`
- `prior_rank`
- `vector_similarity`
- `cross_encoder`
- `mmr`
- `group_suppression`

重要边界：`Scorer` 只产 `ScoreSignal`，不直接决定最终排序。

### RankedCandidate

已经进入排序结果链路的候选。

```python
RankedCandidate(
    candidate=rank_candidate,
    rank=1,
    score=0.98,
    signals=(...),
    reason="...",
)
```

它由 `Fusion` 首次生成，之后会被 `Reranker` 和 `Diversifier` 继续处理。

`candidate_id` 是便捷属性，本质来自：

```python
ranked.candidate.candidate_id
```

### RankRequest

Ranking Engine 的入参。

```python
RankRequest(
    query=RankQuery(text="如何迁移 tool"),
    candidates=(...),
    top_k=10,
    candidate_limit=100,
)
```

### RankResult

Ranking Engine 的最终返回。

```python
RankResult(
    ranked=(...),
    total_candidates=200,
    pipeline="fielded_text.default",
)
```

工具层拿到 `RankResult` 后，再自己决定怎么格式化成 tool output。

## 插件协议

### Scorer

打分器。

职责：看 query 和 candidates，产出 `ScoreSignal`。

```python
class Scorer(Protocol):
    name: str

    def score(
        self,
        *,
        query: RankQuery,
        candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        ...
```

典型实现：

- `BM25Scorer`
- `FieldedBM25Scorer`
- `KeywordExactScorer`
- `OriginalRankPriorScorer`
- `VectorSimilarityScorer`

Scorer 不应该：

- 改候选
- 截断 top_k
- 直接返回 RankedCandidate
- 做多样性控制

### Fusion

融合器。

职责：把候选和所有 `ScoreSignal` 合成第一版 `RankedCandidate`。

```python
class Fusion(Protocol):
    name: str

    def fuse(
        self,
        *,
        candidates: tuple[RankCandidate, ...],
        signals: tuple[ScoreSignal, ...],
    ) -> tuple[RankedCandidate, ...]:
        ...
```

典型实现：

- `WeightedSumFusion`
- `WeightedRrfFusion`
- `RuleBasedFusion`

Fusion 是必选的，因为没有 Fusion 就没有第一版 ranked list。

### Reranker

重排器。

职责：基于 query 和已有 ranked list 做二次精排。

```python
class Reranker(Protocol):
    name: str

    def rerank(
        self,
        *,
        query: RankQuery,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        ...
```

典型实现：

- `CrossEncoderReranker`
- `LlmListwiseReranker`
- `BgeReranker`

Reranker 可以改变顺序，也可以改变 score，但应该尽量保留原有 signals，方便解释和调试。

### Diversifier

多样性控制器。

职责：避免结果被同一个 group、来源、文档刷屏。

```python
class Diversifier(Protocol):
    name: str

    def diversify(
        self,
        *,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        ...
```

典型实现：

- `MmrDiversifier`
- `NearDedupDiversifier`
- `GroupSuppressionDiversifier`

Diversifier 可以过滤、降权或重排，但应该把原因写进 `reason` 或 `ScoreSignalKind.DIVERSITY` 信号。

## Pipeline

`RankingPipeline` 是插件编排声明。

```python
RankingPipeline(
    name="fielded_text.default",
    fusion=WeightedRrfFusion(),
    scorers=(
        FieldedBM25Scorer(...),
        OriginalRankPriorScorer(),
    ),
    reranker=None,
    diversifier=GroupSuppressionDiversifier(),
)
```

字段含义：

- `name`：pipeline 名称。
- `fusion`：融合器，必填。
- `scorers`：打分器列表，可以为空，但一般至少一个。
- `reranker`：重排器，可选（至多一个）。
- `diversifier`：多样性控制器，可选（至多一个）。

## Engine 执行流程

`RankingEngine` 直接接收业务方组装好的 `RankingPipeline`。

当前流程：

```text
1. 如果 top_k <= 0 或 candidates 为空，直接返回空结果
2. 逐个 scorer 收集 ScoreSignal
3. fusion 生成第一版 RankedCandidate
4. 重新分配 rank
5. 按 candidate_limit 裁剪
6. 可选 reranker 精排
7. rerank 后重新分配 rank，并裁剪 candidate_limit
8. 可选 diversifier 多样性控制
9. 重新分配 rank
10. 裁剪 top_k
11. 返回 RankResult
```

这意味着：

- `candidate_limit` 控制 expensive reranker 前后的候选窗口。
- `top_k` 控制最终返回数量。
- 每个阶段后 engine 都会保证 rank 连续。

## 一个最小例子

假设你有三个候选：

```python
candidates = (
    RankCandidate(candidate_id="a", text="Python ranking engine"),
    RankCandidate(candidate_id="b", text="Java service discovery"),
    RankCandidate(candidate_id="c", text="BM25 ranking pipeline"),
)
```

Scorer 产出：

```text
a -> bm25:text = 1.2
b -> bm25:text = 0.1
c -> bm25:text = 2.0
```

Fusion 融合后：

```text
1. c score=2.0
2. a score=1.2
3. b score=0.1
```

如果有 Diversifier，可能发现 `c` 和 `a` 属于同一个 group，于是降权或过滤其中一个。

最终返回：

```python
RankResult(
    ranked=(
        RankedCandidate(candidate=c, rank=1, score=...),
        RankedCandidate(candidate=a, rank=2, score=...),
    ),
    total_candidates=3,
    pipeline="xxx",
)
```

## 怎么从旧算法迁过来

旧算法对应关系：

```text
tokenize_for_bm25       -> tokenizer 工具类，供 scorer 使用
rank_documents_by_bm25  -> BM25Scorer
score_fielded_bm25      -> FieldedBM25Scorer
weighted_rrf            -> WeightedRrfFusion
select_by_mmr           -> MmrDiversifier
rank_plain_text         -> PlainTextRanking pipeline
rank_evidence           -> 外层 EvidenceRankingService + RankingPipeline
```

注意：`rank_evidence` 不应该整体塞进 engine。它里面有 content_id 解析、content store 读取、chunk 展开、excerpt、context preview，这些都不是 ranking core 的职责。

正确拆法：

```text
EvidenceRankingService
  -> 从 ToolContentStore 取内容
  -> 把 chunk 转成 RankCandidate
  -> 调 RankingEngine
  -> 把 RankedCandidate 转回 RankedEvidence
  -> 生成 excerpt/context_preview/tool output
```

## 当前还没做的事

这套 core 目前只是协议和编排骨架，还没有具体插件。

下一步建议实现顺序：

1. `WeightedSumFusion`
2. `BM25Scorer`
3. `FieldedBM25Scorer`
4. `PriorRankScorer`
5. `WeightedRrfFusion`
6. `GroupLimitDiversifier`
7. `MmrDiversifier`

先把 `BM25Scorer + WeightedSumFusion` 跑通，再做 RRF 和 MMR。

## 最容易混的点

### Scorer 和 Fusion 的区别

Scorer 只是说：“我给这个候选一个分数。”

Fusion 才说：“综合所有分数后，最终先排成这样。”

### Reranker 和 Diversifier 的区别

Reranker 追求更相关。

Diversifier 追求不要重复。

### candidate_limit 和 top_k 的区别

`candidate_limit` 是中间窗口，用来控制 expensive 阶段处理多少候选。

`top_k` 是最终返回数量。

### metadata 不等于协议字段

`metadata` 是给调用方和调试用的，Ranking Engine 不应该依赖具体 metadata key。真正影响排序的内容应该进入明确字段，比如 `text`、`fields`、`prior_rank`、`group_key` 或 `ScoreSignal`。
