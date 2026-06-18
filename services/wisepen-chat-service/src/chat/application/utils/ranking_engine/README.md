# Ranking Engine

Ranking Engine 是工具层共用的排序小框架。它的目标不是展示复杂算法，而是让业务工具把“候选怎么排”从工具代码里拆出来，便于 review、替换和排错。

读这份文档时请抓住一个边界：Ranking Engine 只排序，不读取 Redis，不解析 content_id，不格式化 tool output，也不决定业务流程。

内置 engine 通过 `registry.py` 显式注册并维护单例，不做懒加载或懒导入。

## 什么时候用

适合用 Ranking Engine 的场景：

- `web_search` 合并多个搜索源结果。
- `tool_content_read` 在 chunk 中找相关窗口。
- 文档、网页、候选证据需要 BM25 / prior rank / RRF / 多样性控制。
- 下游业务已经拿到一批候选，只需要可解释排序。

不适合放进 Ranking Engine 的逻辑：

- 读取 ToolContentStore。
- URL 安全校验、文件解析、网页抓取。
- LLM 总结、答案生成。
- tool output 渲染。
- 业务专属判断，例如来源可信度、课程/论文/代码场景规则。

## 执行链路

```text
RankCandidate
  -> Scorer 产出 ScoreSignal
  -> WeightedRrfFusion 融合成 RankedCandidate
  -> optional async Reranker
  -> optional Diversifiers
  -> RankResult
```

目前默认推荐的融合方式是 `WeightedRrfFusion`。不要再新增或回补 weighted sum 作为默认方案。

## 业务接入最小步骤

1. 把业务对象转成 `RankCandidate`。
2. 选择需要的 scorers。
3. 用 `WeightedRrfFusion()` 融合。
4. 如需模型 rerank，调用 `rank_async()`，不要调用同步 `rank()`。
5. 把 `RankedCandidate` 映射回业务输出。

示例：

```python
from chat.application.utils.ranking_engine import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
    RankingPipeline,
)
from chat.application.utils.ranking_engine.fusion import WeightedRrfFusion
from chat.application.utils.ranking_engine.scorers import FieldedBM25Scorer, PriorRankScorer

pipeline = RankingPipeline(
    name="web_search.default",
    scorers=(
        FieldedBM25Scorer(tokenizer=tokenizer, config=fielded_config),
        PriorRankScorer(),
    ),
    fusion=WeightedRrfFusion(),
)

result = RankingEngine(pipeline=pipeline).rank(
    RankRequest(
        query=RankQuery(text="用户真实检索目标", queries=("query 1", "query 2")),
        candidates=candidates,
        top_k=10,
        candidate_limit=50,
    )
)
```

## RankCandidate 怎么填

`RankCandidate` 是业务和排序框架之间最重要的接口。

| 字段 | 什么时候填 | 注意 |
| --- | --- | --- |
| `candidate_id` | 永远必填 | 同一次请求内必须唯一 |
| `text` | 全文 BM25、reranker、MMR 需要主文本时 | 不要把字段文本重复拼进去造成二次计算 |
| `fields` | 字段化 BM25 或关键词字段命中 | key 应该表达真实语义，如 `title`、`snippet`、`section`、`anchor` |
| `prior_rank` | 上游已有排序时 | 越小越靠前，`None` 会被 PriorRankScorer 跳过 |
| `group_key` | 需要多样性控制时 | web 用 domain，文档 chunk 用 document/content id |
| `metadata` | 回填业务信息 | Ranking Engine 不应该依赖模糊 metadata 做核心排序 |

常见坑：

- 有 `fields` 时，不代表 `text` 会自动参与 `FieldedBM25Scorer`。`FieldedBM25Scorer` 只读取 `candidate.fields[field_name]`。
- 如果同一段正文同时放进 `text` 和 `fields["body"]`，又同时启用 `BM25Scorer` 和 `FieldedBM25Scorer(body)`，就是重复算正文。
- 字段名不要硬凑。比如 chunk 的章节路径应该叫 `section`，锚点应该叫 `anchor`，不要伪装成 `title` 或 `summary`。

## 现有组件

### Scorers

| 组件 | 用途 | 必要输入 |
| --- | --- | --- |
| `BM25Scorer` | 对 `candidate.text` 做 BM25 | `text` 非空更有意义 |
| `FieldedBM25Scorer` | 对配置中的 `fields` 分别做 BM25 | `candidate.fields` 中存在对应 key |
| `PriorRankScorer` | 把上游原始排名转成信号 | `prior_rank` |
| `KeywordScorer` | query metadata 中关键词精确命中 | `query.metadata["keywords"]` 必须是 list/tuple |
| `DenseVectorScorer` | query/candidate embedding 余弦相似度 | `metadata["embedding"]` |

### Fusion

| 组件 | 用途 |
| --- | --- |
| `WeightedRrfFusion` | 推荐默认融合。按 `signal.weight / (k + rank)` 聚合多路信号 |

### Rerankers

| 组件 | 用途 | 注意 |
| --- | --- | --- |
| `CrossEncoderReranker` | sentence-transformers CrossEncoder 精排 | 会加载模型，测试应注入 fake model |
| `BgeReranker` | FlagEmbedding reranker 精排 | 会加载模型，测试应注入 fake model |
| `ZeroEntropyReranker` | LLM rerank | 需要候选主文本可靠 |

Reranker 协议是 async。只要 pipeline 带 reranker，就必须调用：

```python
await RankingEngine(pipeline=pipeline).rank_async(request)
```

同步 `rank()` 遇到 reranker 会直接报错。

### Diversifiers

| 组件 | 用途 |
| --- | --- |
| `MmrDiversifier` | relevance 和文本相似度之间做 MMR 折中 |
| `GroupRoundRobinDiversifier` | 按 group 轮转，避免同组霸榜 |
| `MaxMinDiversifier` | 最大化结果之间的差异 |

## 组件装配建议

### web_search

```text
FieldedBM25Scorer(title/snippet/url_path/domain)
+ PriorRankScorer
+ WeightedRrfFusion
+ domain diversifiers 或 domain cap
```

### tool_content_read ranked_expand

```text
BM25Scorer(text)
+ FieldedBM25Scorer(section/anchor)
+ WeightedRrfFusion
```

这里保留两个 scorer 是为了避免把 chunk 正文和结构字段混成一个字段集。`BM25Scorer` 算正文，`FieldedBM25Scorer` 算 section/anchor，不会重复计算 section。

### 已经有 embedding 的候选

```text
DenseVectorScorer
+ FieldedBM25Scorer 或 PriorRankScorer
+ WeightedRrfFusion
```

`DenseVectorScorer` 不负责在线补 embedding。上游必须提前把向量放进 metadata。

## candidate_limit 和 top_k

- `candidate_limit` 是中间窗口，控制 reranker/diversifier 前最多保留多少候选。
- `top_k` 是最终返回数量。

如果结果为空，先看：

- `top_k` 是否为 0。
- `candidate_limit` 是否为 0。
- scorer 是否因为缺字段没有产信号。
- fusion 是否只有无效 candidate_id 的 signal。

## Review 时重点看什么

- 候选是否有稳定、唯一的 `candidate_id`。
- 是否把同一段文本重复放进多个 scorer 里算。
- 字段名是否真实表达业务含义。
- reranker pipeline 是否使用 `rank_async()`。
- embedding / keywords 是否由上游显式提供。
- tool 代码里是否又手写了一套排序逻辑。

## 不要做

- 不要在 Ranking Engine 里读取 Redis 或 ToolContentStore。
- 不要在 scorer 里调用外部业务服务补数据。
- 不要为了“通用”把业务规则塞进 metadata 魔法 key。
- 不要让插件裁剪 top_k，裁剪由 `RankingEngine` 负责。
- 不要让 scorer 返回 `RankedCandidate`。
