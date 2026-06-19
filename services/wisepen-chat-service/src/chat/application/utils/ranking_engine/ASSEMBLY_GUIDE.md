# Ranking Engine 组装指南

这份文档只回答一个问题：业务工具要怎么把现有 Ranking Engine 组件装起来，并且不踩坑。

它不是算法介绍，也不是组件大全。示例必须能对应当前代码。

## 当前真实能力

当前 pipeline 结构是：

```python
RankingPipeline(
    name="...",
    scorers=(...),             # 可多个
    fusion=WeightedRrfFusion(), # 必填，当前唯一推荐 fusion
    reranker=None,             # 可选，最多一个，异步
    diversifiers=(),           # 可多个，按声明顺序执行
)
```

执行顺序：

```text
scorers -> fusion -> candidate_limit -> reranker(rank_async only) -> diversifiers -> top_k
```

重点限制：

- `scorers` 可以多个。
- `fusion` 当前使用 `WeightedRrfFusion`。
- `reranker` 最多一个，并且是 async；pipeline 带 reranker 时必须调用 `rank_async()`。
- `diversifiers` 可以多个，会按 tuple 声明顺序依次执行。
- 插件不负责裁剪 top_k，裁剪由 `RankingEngine` 做。

## 最小可运行示例

```python
from chat.application.utils.ranking_engine import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
    RankingPipeline,
)
from chat.application.utils.ranking_engine.fusion import WeightedRrfFusion
from chat.application.utils.ranking_engine.scorers import BM25Scorer, PriorRankScorer
from chat.application.utils.ranking_engine.text import RankingTokenizer

tokenizer = RankingTokenizer()

pipeline = RankingPipeline(
    name="demo.text",
    scorers=(
        BM25Scorer(tokenizer=tokenizer),
        PriorRankScorer(),
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

| 字段 | 用途 | 谁会用 |
| --- | --- | --- |
| `candidate_id` | 候选唯一 ID | 全链路 |
| `text` | 主文本 | `BM25Scorer`、reranker、部分 diversifier |
| `fields` | 字段文本 | `FieldedBM25Scorer`、`KeywordScorer` |
| `prior_rank` | 上游原始排序 | `PriorRankScorer` |
| `group_key` | 多样性分组 | diversifier |
| `metadata` | 业务回填信息 | `DenseVectorScorer` 读取 `embedding`，其他多数不解释 |

常见错误：

- 不要把同一段正文同时放进 `text` 和 `fields["body"]`，又同时启用 `BM25Scorer` 和 `FieldedBM25Scorer(body)`。
- 不要硬凑字段名。比如章节路径用 `section`，锚点用 `anchor`，不要伪装成 `title/summary`。
- `candidate_id` 重复会报错。

## Scorer 选型

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

### PriorRankScorer

看 `candidate.prior_rank`。

```python
PriorRankScorer()
```

`prior_rank=None` 的候选会跳过该信号。

### KeywordScorer

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
KeywordScorerConfig(
    signal_name="keyword:match",
    text_weight=1.0,
    field_weights=(
        ("title", 3.0),
        ("heading", 2.0),
        ("summary", 1.5),
    ),
    case_sensitive=False,
    normalize_unicode=True,
    min_score=0.0,
    require_all_keywords=False,
)
```

示例：

```python
KeywordScorer(
    config=KeywordScorerConfig(
        text_weight=1.0,
        field_weights=(
            ("title", 3.0),
            ("section", 2.0),
            ("anchor", 1.5),
        ),
        require_all_keywords=False,
    )
)
```

注意：这里没有 `KeywordMatchTarget`，也没有 `keyword_metadata_key`。不要照旧示例写。

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

为什么用 RRF：BM25、关键词、向量、prior 的原始分数不是同一量纲，不能简单相加。

## Reranker

当前 reranker 是 async 协议。带 reranker 时：

```python
result = await engine.rank_async(request)
```

不要调用同步 `rank()`。

可选组件：

- `CrossEncoderReranker`
- `BgeReranker`
- `ZeroEntropyReranker`

示例：

```python
pipeline = RankingPipeline(
    name="kb.bge",
    scorers=(FieldedBM25Scorer(tokenizer=tokenizer), PriorRankScorer()),
    fusion=WeightedRrfFusion(),
    reranker=BgeReranker(
        config=BgeRerankerConfig(
            max_candidates=50,
            keep_original_on_failure=True,
        )
    ),
)

result = await RankingEngine(pipeline=pipeline).rank_async(request)
```

测试 reranker 时要注入 fake model，不要加载真实模型。

## Diversifier

pipeline 支持多个 diversifier，按 tuple 声明顺序依次执行：

```python
diversifiers=(
    GroupRoundRobinDiversifier(),
    MmrDiversifier(tokenizer=tokenizer),
)
```

可选组件：

| 组件 | 适合场景 |
| --- | --- |
| `GroupRoundRobinDiversifier` | 按 `group_key` 打散同组结果 |
| `MmrDiversifier` | 文本近重复、同文档 chunk 去重 |
| `MaxMinDiversifier` | 有 embedding 时做语义多样性 |

多个 diversifier 串联时，前一个插件输出的排序结果会作为后一个插件输入。

## 推荐模板

### tool_content_read ranked_expand

```python
RankingPipeline(
    name="tool_content_read.ranked_expand",
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
        "anchor": " ".join(anchor_names),
    },
    metadata={"chunk_index": chunk_index},
)
```

这里保留两个 scorer：`BM25Scorer` 看正文，`FieldedBM25Scorer` 看结构字段，不会重复计算 section。

### web_search

```python
RankingPipeline(
    name="web_search.default",
    scorers=(
        FieldedBM25Scorer(
            tokenizer=tokenizer,
            config=FieldedBM25ScorerConfig(
                field_weights={
                    "title": 3.0,
                    "snippet": 1.0,
                    "url_path": 0.3,
                    "domain": 0.5,
                }
            ),
        ),
        PriorRankScorer(),
    ),
    fusion=WeightedRrfFusion(),
    diversifiers=(GroupRoundRobinDiversifier(),),
)
```

候选建议：

```python
RankCandidate(
    candidate_id=canonical_url_hash,
    text=f"{title}\n{snippet}",
    fields={
        "title": title,
        "snippet": snippet,
        "url_path": cleaned_url_path,
        "domain": domain,
    },
    prior_rank=source_rank,
    group_key=domain,
    metadata={"url": url, "source_id": source_id},
)
```

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

| 场景 | candidate_limit | top_k |
| --- | --- | --- |
| 纯 BM25/RRF | 100 到 300 | 业务需要多少就设多少 |
| 本地 reranker | 20 到 100 | 5 到 20 |
| 外部 API reranker | 20 到 50 | 5 到 20 |
| 多样性明显重要 | 至少是 top_k 的 3 倍 | 最终展示数量 |

## Review 清单

- 示例代码里的 config 字段是否真实存在。
- pipeline 是否只配置一个 reranker，多个 diversifier 的顺序是否符合业务意图。
- 带 reranker 的调用是否用 `rank_async()`。
- `KeywordScorer` 是否显式传了 `query.metadata["keywords"]`。
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
