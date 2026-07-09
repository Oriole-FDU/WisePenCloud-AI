# Ranking Engine 组装指南

这份文档只回答一个问题：业务工具要怎么把现有 Ranking Engine 组件装起来，并且不踩坑。

它不是算法介绍，也不是组件大全。示例必须能对应当前代码。

## 当前真实能力

当前 pipeline 结构是：

```python
RankingPipeline(
    name="...",
    filters=(...),  # 可多个，先于 scorer 执行
    scorers=(...),  # 可多个
    fusion=WeightedRrfFusion(),  # 必填，当前唯一推荐 fusion
    reranker=None,  # 可选，最多一个，异步
    diversifiers=(),  # 可多个，按声明顺序执行
)
```

执行顺序：

```text
filters -> scorers -> fusion -> candidate_limit -> reranker(rank_async only) -> diversifiers -> top_k
```

重点限制：

- `scorers` 可以多个。
- `filters` 可以多个，用于硬约束筛选，不产生排序分数。
- `fusion` 当前使用 `WeightedRrfFusion`。
- `reranker` 最多一个，并且是 async；pipeline 带 reranker 时必须调用 `rank_async()`。
- `diversifiers` 可以多个，会按 tuple 声明顺序依次执行。
- 插件不负责裁剪 top_k，裁剪由 `RankingEngine` 做。

## 最小可运行示例

```python
from chat.application.utils.ranking_engine.engine import RankingEngine
from chat.application.utils.ranking_engine.filters import KeywordFilter, KeywordFilterConfig
from chat.application.utils.ranking_engine.fusion import WeightedRrfFusion
from chat.application.utils.ranking_engine.models import RankCandidate, RankQuery, RankRequest
from chat.application.utils.ranking_engine.pipeline import RankingPipeline
from chat.application.utils.ranking_engine.scorers import BM25Scorer
from chat.application.utils.ranking_engine.tokenizer import ThuLacRankingTokenizer

tokenizer = ThuLacRankingTokenizer()

pipeline = RankingPipeline(
    name="demo.tokenizer",
    scorers=(
        BM25Scorer(tokenizer=tokenizer),
    ),
    fusion=WeightedRrfFusion(),
)

engine = RankingEngine(pipeline=pipeline)

result = engine.rank(
    RankRequest(
        query=RankQuery(text="ranking engine 怎么组装"),
        candidates=(
            RankCandidate(candidate_id="a", text="Ranking Engine 使用 scorer 和 fusion 排序", prior_rank=1),
            RankCandidate(candidate_id="b", text="普通工具输出说明", prior_rank=2),
        ),
        top_k=5,
        candidate_limit=50,
    )
)
```

## RankCandidate 怎么填

| 字段             | 用途      | 谁会用                                        |
|----------------|---------|--------------------------------------------|
| `candidate_id` | 候选唯一 ID | 全链路                                        |
| `text`         | 主文本     | `BM25Scorer`、reranker、部分 diversifier       |
| `fields`       | 字段文本    | `FieldedBM25Scorer`、`KeywordFilter`        |
| `prior_rank`   | 上游原始排序  | 调用方可在业务边界转成 `ScoreSignal` |
| `group_key`    | 多样性分组   | diversifier                                |
| `metadata`     | 业务回填信息  | `DenseVectorScorer` 读取 `embedding`，其他多数不解释 |

常见错误：

- 不要把同一段正文同时放进 `text` 和 `fields["body"]`，又同时启用 `BM25Scorer` 和 `FieldedBM25Scorer(body)`。
- 不要硬凑字段名。比如章节路径用 `section`，锚点用 `anchor`，不要伪装成 `title/summary`。
- `candidate_id` 重复会报错。

## Lexical Scorer 选型

### BM25Scorer

看 `candidate.text`。

适合纯文本 chunk、网页正文、普通候选文本。

```python
BM25Scorer(tokenizer=tokenizer)
```

### FieldedBM25Scorer

只看 `candidate.fields`，不会自动读取 `candidate.text`。

默认字段：

```python
FieldedBM25ScorerConfig(
    field_weights={
        "title": 3.0,
        "heading": 2.0,
        "summary": 1.5,
    }
)
```

自定义字段示例：

```python
FieldedBM25Scorer(
    tokenizer=tokenizer,
    config=FieldedBM25ScorerConfig(
        field_weights={
            "section": 2.0,
            "anchor": 1.5,
        }
    ),
)
```

适合结构字段真实存在的场景。字段不存在或为空会跳过。

## 上游排序信号

如果上游检索系统已经给出原始 rank / score，调用方应在业务边界把它转换成
`ScoreSignal`，通过 `RankRequest.signals` 交给 `WeightedRrfFusion`，不要塞进
`candidate.metadata` 让 Ranking Engine 猜测。

请求侧：

```python
from chat.application.utils.ranking_engine.models import ScoreSignal, ScoreSignalKind

RankCandidate(
    candidate_id="chunk-a",
    text="...",
)

ScoreSignal(
    candidate_id="chunk-a",
    name="qdrant:dense",
    value=0.82,
    kind=ScoreSignalKind.VECTOR,
    rank=1,
    weight=1.0,
)
```

现有 `rag.knowledge_search` 就是这个模式：`RagEvidenceRankingService` 把 Qdrant
召回产生的 dense / sparse channel 信号转换成 `ScoreSignal`，再进入
`rag.knowledge_search` pipeline 做 RRF、rerank 和 diversify。

## Filter 选型

### KeywordFilter

关键词必须由上游显式传入：

```python
RankQuery(
    text="",
    metadata={"keywords": ("APIError", "timeout")},
)
```

`keywords` 必须是 `list` 或 `tuple`。单个关键词也要写成 `("keyword",)` 或 `["keyword"]`，不能直接传字符串。

当前配置字段是：

```python
KeywordFilterConfig(
    text_enabled=True,
    field_names=(
        "title",
        "heading",
        "summary",
        "section",
        "anchor",
        "indexing_text",
    ),
    case_sensitive=False,
    normalize_unicode=True,
    require_all_keywords=False,
)
```

示例：

```python
RankingPipeline(
    name="anchored.keyword",
    filters=(
        KeywordFilter(
            config=KeywordFilterConfig(
                field_names=("title", "section", "anchor", "indexing_text"),
                require_all_keywords=True,
            )
        ),
    ),
    scorers=(BM25Scorer(tokenizer=tokenizer),),
    fusion=WeightedRrfFusion(),
)
```

注意：关键词精确命中是过滤器，不再作为 scorer 参与 RRF；它只决定候选是否保留，不给候选加分。

## Vector Scorer 选型

### DenseVectorScorer

固定读取：

- `query.metadata["embedding"]`
- `candidate.metadata["embedding"]`

缺失会抛 `ValueError`。它不会在线生成 embedding。

配置：

```python
DenseVectorScorerConfig(
    signal_name="dense:cosine",
    weight=1.0,
    min_score=0.0,
)
```

示例：

```python
DenseVectorScorer(
    config=DenseVectorScorerConfig(weight=1.5)
)
```

请求侧：

```python
RankQuery(
    text="检索排序",
    metadata={"embedding": query_embedding},
)

RankCandidate(
    candidate_id="a",
    text="...",
    metadata={"embedding": candidate_embedding},
)
```

## Fusion

当前使用：

```python
WeightedRrfFusion(k=60.0)
```

它只使用带 `rank` 的 signal：

```text
contribution = signal.weight / (k + signal.rank)
```

没有 rank 的 signal 不参与融合。

为什么用 RRF：BM25、向量、prior 的原始分数不是同一量纲，不能简单相加。

## BM25 索引复用与缓存

`BM25Scorer` 和 `FieldedBM25Scorer` 当前按请求候选集合临时构建 BM25 索引。
这是有意的：Ranking Engine 面向调用方传入的短生命周期候选集，不拥有稳定语料库。

不要在 scorer 内部随手加通用 LRU 缓存：

- 缓存 key 需要覆盖候选顺序、文本、字段、tokenizer 配置和 BM25 参数，容易膨胀。
- 候选通常来自一次 tool read / retrieval 的窗口，生命周期短，缓存命中不稳定。
- scorer 不是语料库索引所有者，不应长期持有大索引。

如果业务场景有稳定大语料库，优先在检索层维护 BM25 索引，而不是在 Ranking
Engine scorer 中缓存。当前使用的 `bm25s` 已内置 `save()`、`load()`、
`load_scores()` 和 `mmap=True`，官方文档也把 mmap 作为大索引低内存加载方案；
它适合“预构建索引 → 持久化 → mmap 加载 → 查询”的长期索引复用模式。这类能力应放在
Elasticsearch/Qdrant/RAG 检索仓储或专门索引服务边界，不放在 request-scoped scorer 里。

## Reranker

当前 reranker 是 async 协议。带 reranker 时：

```python
result = await engine.rank_async(request)
```

不要调用同步 `rank()`。

当前实现的组件：

- `ZeroEntropyReranker`

示例：

```python
from chat.application.utils.ranking_engine.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)

pipeline = RankingPipeline(
    name="kb.zero_entropy",
    scorers=(FieldedBM25Scorer(tokenizer=tokenizer),),
    fusion=WeightedRrfFusion(),
    reranker=ZeroEntropyReranker(
        client=zero_entropy_client,
        config=ZeroEntropyRerankerConfig(
            model="your-rerank-model",
            top_n=20,
        ),
    ),
)

result = await RankingEngine(pipeline=pipeline).rank_async(request)
```

测试 reranker 时要注入 fake model，不要加载真实模型。

## Diversifier

pipeline 支持多个 diversifier，按 tuple 声明顺序依次执行：

```python
diversifiers = (
    GroupRoundRobinDiversifier(),
    MmrDiversifier(tokenizer=tokenizer),
)
```

可选组件：

| 组件                           | 适合场景                 |
|------------------------------|----------------------|
| `GroupRoundRobinDiversifier` | 按 `group_key` 打散同组结果 |
| `MmrDiversifier`             | 文本近重复、同文档 chunk 去重   |
| `MaxMinDiversifier`          | 有 embedding 时做语义多样性  |

多个 diversifier 串联时，前一个插件输出的排序结果会作为后一个插件输入。

## 推荐模板

### read.ranked_expand

```python
RankingPipeline(
    name="read.ranked_expand",
    scorers=(
        BM25Scorer(tokenizer=tokenizer),
        FieldedBM25Scorer(
            tokenizer=tokenizer,
            config=FieldedBM25ScorerConfig(
                field_weights={"section": 2.0, "anchor": 1.5}
            ),
        ),
    ),
    fusion=WeightedRrfFusion(),
)
```

候选应这样组装：

```python
RankCandidate(
    candidate_id=f"{content_id}:chunk:{chunk_index}",
    text=chunk_text,
    fields={
        "section": " / ".join(section_path),
        "anchor": " ".join(anchor_labels),
    },
    metadata={"chunk_index": chunk_index},
)
```

这里保留两个 scorer：`BM25Scorer` 看正文，`FieldedBM25Scorer` 看结构字段，不会重复计算 section。

### 有 embedding 的 hybrid 排序

```python
RankingPipeline(
    name="hybrid.lexical_dense",
    scorers=(
        BM25Scorer(tokenizer=tokenizer),
        DenseVectorScorer(config=DenseVectorScorerConfig(weight=1.5)),
    ),
    fusion=WeightedRrfFusion(),
    diversifiers=(MaxMinDiversifier(),),
)
```

前提：query 和每个 candidate 都有 `metadata["embedding"]`。

## candidate_limit 和 top_k

- `candidate_limit`: 中间窗口，控制 reranker/diversifier 前保留多少候选。
- `top_k`: 最终返回数量。

建议：

| 场景              | candidate_limit | top_k      |
|-----------------|-----------------|------------|
| 纯 BM25/RRF      | 100 到 300       | 业务需要多少就设多少 |
| 本地 reranker     | 20 到 100        | 5 到 20     |
| 外部 API reranker | 20 到 50         | 5 到 20     |
| 多样性明显重要         | 至少是 top_k 的 3 倍 | 最终展示数量     |

## Review 清单

- 示例代码里的 config 字段是否真实存在。
- pipeline 是否只配置一个 reranker，多个 diversifier 的顺序是否符合业务意图。
- 带 reranker 的调用是否用 `rank_async()`。
- `KeywordFilter` 是否显式传了 `query.metadata["keywords"]`。
- `DenseVectorScorer` 是否提前准备了 query/candidate embedding。
- 是否重复计算同一段正文。
- 字段名是否表达真实业务语义。
- 插件里是否私自裁剪 top_k。

## 不要做

- 不要把 ToolContentStore、Redis、URL fetch 放进 Ranking Engine。
- 不要在 scorer 里临时生成 embedding。
- 不要为了“灵活”写不存在的 config 字段。
- 不要让 diversifier 私自裁剪 top_k。
- 不要用 metadata 魔法 key 替代明确字段。
