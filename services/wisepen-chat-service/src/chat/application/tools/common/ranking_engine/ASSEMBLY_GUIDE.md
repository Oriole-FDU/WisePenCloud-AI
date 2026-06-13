# Ranking Engine 组装与组件选型指南

这份文档说明 `ranking_engine` 目录下的组件如何组合成可运行的排序流水线，以及不同场景下应该选择哪些组件。

已有的 `core/README.md` 更偏核心机制说明；本文更偏装配手册和选型决策。

## 1. 总体装配顺序

当前 Ranking Engine 的固定执行顺序是：

```text
RankRequest
  -> Scorer 产出 ScoreSignal
  -> Fusion 融合成初排 RankedCandidate
  -> Reranker 做精排
  -> Diversifier 做多样性控制
  -> RankResult
```

也就是：

1. 调用方把业务对象转换成 `RankCandidate`。
2. 一个或多个 `Scorer` 从不同角度打分。
3. 一个 `Fusion` 把多路信号融合成初始排序。
4. 可选 `Reranker` 对前若干候选做模型精排。
5. 可选 `Diversifier` 控制重复、同组霸榜和相似内容。
6. `RankingEngine` 统一补 rank、裁剪 `candidate_limit` 和 `top_k`。

## 2. 输入候选怎么组装

`RankCandidate` 是 engine 唯一认可的候选输入。外部工具、搜索结果、chunk、document 都要先转成它。

```python
RankCandidate(
    candidate_id="chunk-123",
    text="正文或 chunk 文本",
    fields={
        "title": "文档标题",
        "heading": "章节路径",
        "summary": "摘要",
        "body": "正文",
    },
    prior_rank=1,
    group_key="doc-456",
    metadata={
        "embedding": [0.1, 0.2, 0.3],
        "source": "knowledge_base",
    },
)
```

字段选择建议：

| 字段 | 什么时候填 | 被谁使用 |
| --- | --- | --- |
| `candidate_id` | 必填，必须唯一 | 全链路 |
| `text` | 有主文本、chunk 文本、模型重排文本时 | `BM25Scorer`、`DenseVectorScorer`、reranker、部分 diversifier |
| `fields` | 有标题、路径、摘要、正文等结构化文本时 | `FieldedBM25Scorer`、`KeywordScorer`、reranker fallback |
| `prior_rank` | 上游已有排序，比如 ES/向量库/数据库返回顺序 | `PriorRankScorer` |
| `group_key` | 需要避免同文档、同来源、同父 chunk 连续霸榜 | `GroupRoundRobinDiversifier`、`MmrDiversifier` |
| `metadata["embedding"]` | 已有向量，或要用向量相似度/向量多样性 | `DenseVectorScorer`、`MaxMinDiversifier` |

不要把业务逻辑塞进 `metadata` 后再让 engine 猜。真正影响排序的内容优先放到明确字段：`text`、`fields`、`prior_rank`、`group_key`。

## 3. Tokenizer 和词典如何选择

`RankingTokenizer` 主要服务词法打分和文本相似度，包括：

- 中英文混合 token 提取。
- NFKC 归一化。
- 英文 casefold。
- 中文 jieba 搜索模式分词。
- 中文 bigram。
- 复合词拆分，比如 `tool-call`、`api/v1`。
- stopwords 过滤。
- 领域词保护。

默认 tokenizer 适合大多数中文、英文、中英混合排序：

```python
tokenizer = RankingTokenizer()
```

默认不维护项目本地词表文件。需要通用领域词时，可以加载已有的外部词表资源，例如 THUOCL：

```python
from pathlib import Path

from ranking_engine.text import RankingTokenizer, load_thuocl_domain_lexicon

lexicon = load_thuocl_domain_lexicon(data_dir=Path("data/thuocl"))
tokenizer = RankingTokenizer(domain_lexicon=lexicon)
```

选择建议：

| 场景 | tokenizer 配置建议 |
| --- | --- |
| BM25/字段 BM25 | 使用默认 `deduplicate=False`，保留词频信息 |
| 关键词覆盖率 | 可使用默认 tokenizer，或在 `KeywordScorer` 里从 query metadata 明确传 keywords |
| 短中文 query | 保留 `enable_cjk_segmentation=True` 和 `enable_cjk_bigram=True` |
| 代码、接口、路径类文本 | 保留 `split_common_separators=True` 和 `keep_compound_token=True` |
| token 过多的长文本 | 设置 `max_tokens` 控制成本 |

## 4. Scorer 如何选择

Scorer 只负责产出 `ScoreSignal`，不直接决定最终排序。

打分器不要按“越多越好”来堆。每个 scorer 应该代表一种不同证据：

- `BM25Scorer`：正文词法相关。
- `FieldedBM25Scorer`：标题、章节、摘要、正文等字段化词法相关。
- `KeywordScorer`：规则型硬命中。
- `PriorRankScorer`：上游召回顺序先验。
- `DenseVectorScorer`：语义相似度。

### 4.0 打分器选型总表

| Scorer | 主要看什么 | 最适合的候选形态 | 优点 | 风险 | 常见搭配 |
| --- | --- | --- | --- | --- | --- |
| `BM25Scorer` | `candidate.text` 的词频、稀有词、查询词覆盖 | 纯文本 chunk、正文片段 | 稳定、便宜、可解释 | 不懂同义表达；字段差异无法加权 | `PriorRankScorer`、`DenseVectorScorer` |
| `FieldedBM25Scorer` | `candidate.fields` 中不同字段的 BM25 | 文档、FAQ、知识库 chunk | 能突出标题/章节/摘要命中 | 字段填得差会放大噪声 | `KeywordScorer`、`PriorRankScorer` |
| `KeywordScorer` | query keywords 是否被包含 | 有专有名词、错误码、接口名的文本 | 能补硬规则，保护精确命中 | 过强会把“包含但不相关”的结果顶上来 | `FieldedBM25Scorer`、`DenseVectorScorer` |
| `PriorRankScorer` | `prior_rank` | 上游检索器已有排序 | 保留召回器经验，避免重排完全推翻 | 上游排序差会污染结果 | 几乎可搭配所有 scorer |
| `DenseVectorScorer` | embedding cosine similarity | 已有 query/candidate embedding | 擅长语义、同义、改写 | 可能弱化精确约束；临时 embedding 成本高 | `BM25Scorer`、`KeywordScorer` |

### 4.0.1 打分器组合策略

常见组合不是随便叠加，而是按召回证据互补：

| 场景 | 推荐 scorer 组合 | 原因 |
| --- | --- | --- |
| 简单纯文本排序 | `BM25Scorer + PriorRankScorer` | BM25 提供词法相关，prior 保留上游顺序 |
| 文档/知识库搜索 | `FieldedBM25Scorer + KeywordScorer + PriorRankScorer` | 字段权重表达文档结构，关键词保护术语，上游顺序做先验 |
| 语义检索增强 | `BM25Scorer + DenseVectorScorer + PriorRankScorer` | 同时覆盖字面命中、语义相似和召回先验 |
| 专有名词强约束 | `KeywordScorer + FieldedBM25Scorer` | 先保证术语命中，再按字段相关性拉开差距 |
| 多路召回融合 | `PriorRankScorer + BM25Scorer 或 DenseVectorScorer` | 上游 rank 不能丢，但需要本地 query-candidate 证据校正 |
| reranker 前初筛 | `FieldedBM25Scorer + PriorRankScorer` | 用便宜信号把候选窗口裁到模型可处理范围 |

如果 `BM25Scorer` 和 `FieldedBM25Scorer` 同时使用，要确认它们不是在重复看同一份文本。默认 `FieldedBM25Scorer` 只看 `title/heading/summary`，正文交给 `BM25Scorer` 看 `candidate.text`；只有明确不使用全文 BM25 或确实需要字段正文证据时，才显式把 `body` 加进 `field_weights`。

### 4.1 `BM25Scorer`

基于 `candidate.text` 做全文 BM25。

适合：

- 候选主要是一段正文或 chunk。
- 没有明确字段权重。
- 需要稳定、低成本的词法相关性。
- query 中的词必须真的出现在候选里才算强相关，比如代码、报错、配置项、中文术语。

不适合：

- 标题、摘要、正文权重差异很重要。
- 候选没有 `text`。
- 用户经常用同义词、概念性问法，而候选文本不包含相同字面词。
- 候选文本很短且词面高度相似，BM25 很难区分质量。

关键参数：

| 参数 | 作用 | 选择建议 |
| --- | --- | --- |
| `weight` | 信号权重 | 单独使用可为 `1.0`；和字段/向量混用时按重要性调 |
| `retrieve_k` | 每个 query 最多取多少个 BM25 结果 | 候选很多时设为 `candidate_limit` 或略大 |
| `min_score` | 过滤低分信号 | 默认 `0.0` 通常够用 |
搭配建议：

- 和 `PriorRankScorer` 搭配：适合上游已经召回一批相关候选，本地 BM25 只做校正。
- 和 `DenseVectorScorer` 搭配：适合语义召回和字面匹配都重要的混合排序。
- 和 `MmrDiversifier` 搭配：适合 chunk 文本重复较多的知识库。

### 4.2 `FieldedBM25Scorer`

基于 `candidate.fields` 分字段 BM25，默认字段权重：

```python
{
    "title": 3.0,
    "heading": 2.0,
    "summary": 1.5,
}
```

适合：

- 候选有标题、章节、摘要等结构化字段。
- 标题命中明显应该比正文命中更重要。
- 知识库、文档检索、FAQ 检索。
- 候选字段质量稳定，字段之间有明确业务含义。

不适合：

- 所有候选只有一段纯文本。
- `fields` 只是随便拼出来的重复内容，字段权重没有真实意义。
- 标题或摘要来自不可靠生成结果，可能误导排序。

选择建议：

- 标题短且可信：`title` 权重 3 到 5。
- 章节路径可表达上下文：`heading` 权重 1.5 到 3。
- 摘要质量高：`summary` 权重可以适当提高。
- 正文需要字段化打分：显式加入 `"body": 1.0`，但不要再同时用 `BM25Scorer` 重复看同一份正文。

搭配建议：

- 知识库默认优先选它，而不是 `BM25Scorer`。
- 和 `KeywordScorer` 搭配时，可以让关键词在 `title/heading` 命中获得更高规则分。
- 和 reranker 搭配时，它负责便宜初筛，reranker 只处理 `candidate_limit` 内的候选。

### 4.3 `KeywordScorer`

基于关键词包含关系做规则命中，支持从 `query.metadata["keywords"]` 取关键词，也支持用 tokenizer 从 query 生成关键词。

适合：

- 产品名、专有名词、错误码、接口名必须命中。
- query rewrite 已经产出关键词列表。
- 需要“硬规则”补强 BM25/向量。
- 法条编号、SKU、函数名、类名、API path、配置 key 这类不能靠语义猜的内容。

不适合：

- 只想要语义相关，不关心字面命中。
- query 被 tokenizer 切得太碎，导致普通词也被当作强规则。
- 关键词命中只说明“提到过”，不能说明“回答了问题”的场景。

关键参数：

| 参数 | 作用 | 选择建议 |
| --- | --- | --- |
| `text_weight` | `candidate.text` 命中贡献 | 主文本命中基础分 |
| `field_weights` | 字段命中贡献 | 用 `("title", 3.0)` 这类二元组声明 |
| `require_all_keywords` | 是否要求全部关键词命中 | 精确过滤场景打开；普通排序关闭 |

搭配建议：

- 必须由上游 query rewrite 显式传 `query.metadata["keywords"]`，且必须是 list/tuple。
- 权重不要一开始设太高。先让它做补强，再根据评测观察是否需要拉高。
- `require_all_keywords=True` 更像过滤条件，适合错误码/编号/精确实体，不适合自然语言问句。

### 4.4 `PriorRankScorer`

把上游排序 `prior_rank` 转成 RRF 风格先验分数：`1 / (k + prior_rank)`。

适合：

- 上游检索器已经有可信排序。
- 需要保留 ES、向量库、数据库召回顺序的影响。
- 多路召回后不希望完全丢掉原始排序。
- 上游召回已经做了权限、时间、业务权重、质量分等 engine 外部逻辑。

不适合：

- 上游顺序没有意义，或只是未排序集合。
- `prior_rank` 来自不同召回源但没有归一化含义，直接混在一起会有偏置。
- 想让 engine 完全基于 query-candidate 相关性重新排序。

选择建议：

- 上游很可信：提高 `weight`。
- 上游只是弱召回：降低 `weight`。
- 默认 `k=60.0` 和 RRF 常用设置一致。

搭配建议：

- 几乎所有线上 pipeline 都可以考虑加入，但权重要跟上游质量绑定。
- 如果上游是向量库排序，再加 `DenseVectorScorer` 可能重复表达同一信号；除非 query/candidate embedding 来源不同或需要本地重算。
- 多路召回时，建议在候选合并阶段先给每路候选合理的 `prior_rank`，不要让某一路天然占据所有前排。

### 4.5 `DenseVectorScorer`

基于 query embedding 和 candidate embedding 计算 cosine similarity。

适合：

- 语义检索、同义表达、长 query。
- 候选已有 embedding。
- lexical 召回对同义改写不敏感。
- 用户问法和文档写法差异较大，例如“怎么上线”对应“部署流程”。

不适合：

- 没有 embedding，也不希望排序阶段临时调用 embedder。
- 需要严格字面匹配。
- 错误码、字段名、函数名、编号等必须精确命中的场景单独依赖它。
- 候选 embedding 模型和 query embedding 模型不一致。

关键参数：

| 参数 | 作用 | 选择建议 |
| --- | --- | --- |
| `weight` | dense 信号权重 | 和 BM25 / keyword 混用时按重要性调 |
| `min_score` | 最小保留分数 | 默认过滤 0 分和负相关 |

`DenseVectorScorer` 固定读取 `metadata["embedding"]`。query 和每个 candidate 都必须由上游提前准备 embedding；排序阶段不自动调用 embedder。

搭配建议：

- 和 `KeywordScorer` 搭配，用关键词约束弥补向量检索的“看起来相关但没命中关键实体”。
- 和 `BM25Scorer` 搭配，是最常见的 hybrid 排序基础。
- 如果候选 embedding 已经来自上游向量库，`DenseVectorScorer` 可以只作为融合信号；不要在排序阶段批量补 candidate embedding。

## 5. Fusion 如何选择

当前实现的融合器是 `WeightedRrfFusion`。

它按每个信号的内部排名计算贡献：

```text
contribution = signal.weight / (k + signal.rank)
```

然后对同一个 candidate 的所有贡献求和。

适合：

- 多个 scorer 分数尺度不同，比如 BM25、keyword、prior、dense。
- 你更信任“各路排名位置”，而不是原始分值大小。
- 希望融合结果稳定、容易解释。

关键参数：

| 参数 | 作用 | 选择建议 |
| --- | --- | --- |
| `k` | 控制排名差异的衰减速度 | 默认 `60.0` 稳定；想让头部差异更明显可降低 |

当前没有加权求和 Fusion。由于 BM25、向量、规则分数尺度不同，默认优先用 RRF 是合理选择。

融合阶段的核心问题是：不同 scorer 的原始分数不能直接比较。BM25 分数、cosine 分数、关键词命中次数、prior 分数不是同一个量纲。`WeightedRrfFusion` 用 rank 融合，正好规避这个问题。

组件选择结论：

| 情况 | 选择 |
| --- | --- |
| 多 scorer 混合 | 用 `WeightedRrfFusion` |
| 只有一个 scorer | 仍可用 `WeightedRrfFusion`，相当于按该 scorer 的 rank 初排 |
| scorer 原始分数尺度差异大 | 用 `WeightedRrfFusion`，不要做简单加和 |
| 希望某个 scorer 更重要 | 调 scorer config 里的 `weight` |
| 希望头部 rank 差异更明显 | 降低 `WeightedRrfFusionConfig.k` |

## 6. Reranker 如何选择

Reranker 是成本更高的精排阶段。只应该处理初排后的前若干个候选，由 `candidate_limit` 和 reranker 自身的 `max_candidates` / `top_n` 控制。

注意：只要 pipeline 配了 reranker，就应该调用 `RankingEngine.rank_async()`。同步 `rank()` 遇到 reranker 会报错。

### 6.0 Reranker 选型总表

| Reranker | 部署方式 | 适合场景 | 优点 | 风险 |
| --- | --- | --- | --- | --- |
| `CrossEncoderReranker` | 本地 `sentence-transformers` | 英文/通用 pairwise 精排，本地模型可控 | 不依赖外部 API，模型选择灵活 | 默认模型不一定适合中文；CPU 成本较高 |
| `BgeReranker` | 本地 `FlagEmbedding` | 中文/中英混合知识库精排 | 中文场景通常更合适，BGE 生态成熟 | 模型依赖和硬件成本 |
| `ZeroEntropyReranker` | 外部 API | 不想本地部署模型，已有 ZeroEntropy 接入 | 接入轻，模型托管 | 网络、费用、API 失败和数据出域 |

Reranker 适合放在 scorer/fusion 之后，因为它需要一个小而相对靠谱的候选窗口。不要把 reranker 当召回器使用。

### 6.1 `CrossEncoderReranker`

基于 `sentence-transformers` 的 `CrossEncoder` 本地模型。

适合：

- 可以本地加载 cross encoder 模型。
- 需要 query-document pair 级别精排。
- 对外部 API 依赖敏感。

选择建议：

- 英文或通用场景可用默认 `cross-encoder/ms-marco-MiniLM-L-6-v2`。
- 中文场景应替换成本地或 HuggingFace 上适配中文的 cross encoder。
- CPU 环境控制 `max_candidates` 和 `batch_size`，例如 20 到 50。
- `keep_original_on_failure=True` 适合线上降级。
- 如果打开 `combine_with_original_score`，模型分数不会完全覆盖初排分数，适合初排质量已经较高的场景。

### 6.2 `BgeReranker`

基于 `FlagEmbedding.FlagReranker`。

适合：

- 中文/中英混合检索精排。
- 本地可部署 BGE reranker。
- 希望使用 BAAI/BGE 系列模型。

选择建议：

- 默认 `BAAI/bge-reranker-base` 可作为基础配置。
- 有 GPU 时 `use_fp16=True` 合理；CPU 或兼容性问题可关。
- 线上建议 `keep_original_on_failure=True`。
- 若希望模型分数不要完全覆盖初排，可打开 `combine_with_original_score`。
- 中文知识库、中文 FAQ、中文长文档检索优先试它。

### 6.3 `ZeroEntropyReranker`

基于 ZeroEntropy rerank API 的异步重排。

适合：

- 已经接入 ZeroEntropy。
- 希望把模型托管在外部服务。
- 本地不想部署 reranker 模型。

选择建议：

- `top_n` 控制 API 返回并重排的数量。
- 网络/API 失败会抛 `ZeroEntropyRerankerError`，调用层要处理。
- 只使用 `candidate.text` 作为 documents；如果候选主要在 fields 中，组装候选时应同步填好 `text`。
- 有数据合规要求时，先确认候选文本是否允许发送到外部服务。

## 7. Diversifier 如何选择

Diversifier 负责结果去重、多样化和抑制同组霸榜。它不追求更相关，而是让最终结果覆盖面更好。

### 7.0 Diversifier 选型总表

| Diversifier | 主要依据 | 适合场景 | 优点 | 风险 |
| --- | --- | --- | --- | --- |
| `GroupRoundRobinDiversifier` | `group_key` / metadata group | 同文档、同来源、同 URL 防霸榜 | 简单、稳定、可解释 | 只按组，不识别组内外文本相似 |
| `MmrDiversifier` | 当前分数 + 文本 Jaccard + group 惩罚 | 文本重复、近重复 chunk | 无需 embedding，能平衡相关性和差异 | Jaccard 对语义重复不敏感 |
| `MaxMinDiversifier` | 当前分数 + embedding/text 相似度 | 有 embedding 的语义去重 | 能识别语义相似 | 依赖 embedding 质量，参数更敏感 |

选择原则：

- 只有明确来源分组：先用 `GroupRoundRobinDiversifier`。
- 文本重复明显但没有 embedding：用 `MmrDiversifier`。
- 有高质量 embedding 且需要语义层面的覆盖：用 `MaxMinDiversifier`。
- 可以组合，但通常先从一个 diversifier 开始，避免过度打散相关结果。

### 7.1 `GroupRoundRobinDiversifier`

按 `group_key` 或 metadata group key 分组，并按组轮询取结果。

适合：

- 明确要避免同一文档、同一来源、同一 URL 连续占满结果。
- 需要轻量、确定性、无模型成本。
- group 信息可靠。

选择建议：

- 知识库 chunk 检索通常把 `group_key` 设为 document id。
- `max_per_group_per_round=1` 最能打散同组。
- `preserve_unknown_group=True` 可以避免未知 group 全被当成同一组。

### 7.2 `MmrDiversifier`

使用当前相关性分数和文本 Jaccard 相似度做 MMR 贪心选择；同 `group_key` 会施加较高相似度惩罚。

适合：

- 希望在相关性和文本差异之间折中。
- 候选文本相似度能代表重复程度。
- 没有 embedding，仍想做相似内容抑制。

关键参数：

| 参数 | 作用 | 选择建议 |
| --- | --- | --- |
| `lambda_mult` | 相关性权重，越高越偏相关 | 默认 `0.72`；重复严重可降到 0.5 到 0.65 |
| `same_group_similarity` | 同组惩罚相似度下限 | 同文档 chunk 重复明显时保持 0.9+ |
| `top_k` | 只对前多少个做 MMR 选择 | 一般设为最终 top_k 或留空 |

### 7.3 `MaxMinDiversifier`

基于相关性和“与已选结果的最大相似度”的反向值做贪心选择。相似度优先用 embedding cosine，也可 fallback 到文本 Jaccard。

适合：

- 候选有 embedding，希望按语义差异做多样化。
- 想比 group round-robin 更细粒度地控制相似内容。
- 需要限制只处理 head，tail 保持原序。

关键参数：

| 参数 | 作用 | 选择建议 |
| --- | --- | --- |
| `diversity_weight` | 多样性权重 | 默认 `0.35`；越高越分散 |
| `similarity_metadata_key` | embedding key | 默认 `embedding` |
| `use_embedding_similarity` | 是否优先用向量相似度 | 有 embedding 时打开 |
| `use_text_similarity` | 是否用文本 Jaccard fallback | 没向量或向量缺失时打开 |
| `max_candidates` | 只多样化前 N 个 | 通常设为 `candidate_limit` 或最终 top_k 的 2 到 5 倍 |

## 8. 推荐 Pipeline 模板

### 8.1 纯文本轻量排序

适合小规模候选、无字段、无模型依赖。

```python
from ranking_engine.core import RankingEngine, RankingPipeline
from ranking_engine.fusion import WeightedRrfFusion
from ranking_engine.scorers import BM25Scorer, PriorRankScorer
from ranking_engine.text import RankingTokenizer

tokenizer = RankingTokenizer()

pipeline = RankingPipeline(
    name="text.light",
    scorers=(
        BM25Scorer(tokenizer=tokenizer),
        PriorRankScorer(),
    ),
    fusion=WeightedRrfFusion(),
)

engine = RankingEngine(pipeline=pipeline)
```

### 8.2 文档/知识库字段排序

适合文档 chunk、FAQ、知识库搜索。

```python
from ranking_engine.core import RankingPipeline
from ranking_engine.diversifiers import GroupRoundRobinDiversifier
from ranking_engine.fusion import WeightedRrfFusion
from ranking_engine.scorers import (
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
    KeywordMatchTarget,
    KeywordScorer,
    KeywordScorerConfig,
    PriorRankScorer,
)
from ranking_engine.text import RankingTokenizer

tokenizer = RankingTokenizer()

pipeline = RankingPipeline(
    name="kb.fielded",
    scorers=(
        FieldedBM25Scorer(
            tokenizer=tokenizer,
            config=FieldedBM25ScorerConfig(
                field_weights={
                    "title": 4.0,
                    "heading": 2.0,
                    "summary": 1.5,
                },
            ),
        ),
        KeywordScorer(
            tokenizer=tokenizer,
            config=KeywordScorerConfig(
                targets=(
                    KeywordMatchTarget("text", "text", 1.0),
                    KeywordMatchTarget("field", "title", 3.0),
                    KeywordMatchTarget("field", "heading", 2.0),
                    KeywordMatchTarget("field", "body", 1.0),
                ),
            ),
        ),
        PriorRankScorer(),
    ),
    fusion=WeightedRrfFusion(),
    diversifier=GroupRoundRobinDiversifier(),
)

### 8.3 词法 + 向量混合排序

适合 query 语义改写较多、候选已有 embedding 的场景。

```python
from ranking_engine.core import RankingPipeline
from ranking_engine.diversifiers import MaxMinDiversifier
from ranking_engine.fusion import WeightedRrfFusion
from ranking_engine.scorers import BM25Scorer, DenseVectorScorer, DenseVectorScorerConfig
from ranking_engine.text import RankingTokenizer

tokenizer = RankingTokenizer()

pipeline = RankingPipeline(
    name="hybrid.lexical_dense",
    scorers=(
        BM25Scorer(tokenizer=tokenizer),
        DenseVectorScorer(
            config=DenseVectorScorerConfig(
                embed_candidates_if_missing=False,
                weight=1.5,
            ),
        ),
    ),
    fusion=WeightedRrfFusion(),
    diversifier=MaxMinDiversifier(),
)
```

请求侧需要传 query embedding，候选 metadata 也要有 embedding：

```python
RankQuery(
    text="如何配置知识库检索排序",
    metadata={"embedding": query_embedding},
)
```

### 8.4 带模型精排的高质量排序

适合最终结果质量要求高、可以接受精排成本的场景。

```python
from ranking_engine.core import RankingPipeline
from ranking_engine.diversifiers import MmrDiversifier
from ranking_engine.fusion import WeightedRrfFusion
from ranking_engine.rerankers import BgeReranker, BgeRerankerConfig
from ranking_engine.scorers import FieldedBM25Scorer, PriorRankScorer
from ranking_engine.text import RankingTokenizer

tokenizer = RankingTokenizer()

pipeline = RankingPipeline(
    name="kb.bge_rerank",
    scorers=(
        FieldedBM25Scorer(tokenizer=tokenizer),
        PriorRankScorer(),
    ),
    fusion=WeightedRrfFusion(),
    reranker=BgeReranker(
        config=BgeRerankerConfig(
            max_candidates=50,
            keep_original_on_failure=True,
        ),
    ),
    diversifier=MmrDiversifier(tokenizer=tokenizer),
)
```

调用时必须使用异步入口：

```python
result = await engine.rank_async(request)
```

## 9. `candidate_limit` 和 `top_k` 如何设置

`candidate_limit` 是进入 reranker/diversifier 前的中间窗口，`top_k` 是最终返回数量。

建议：

| 场景 | `candidate_limit` | `top_k` |
| --- | --- | --- |
| 纯 BM25/RRF，无模型 | 100 到 300 | 调用方需要多少就设多少 |
| 本地 reranker CPU | 20 到 50 | 5 到 20 |
| 本地 reranker GPU | 50 到 200 | 5 到 50 |
| 外部 API reranker | 20 到 100，按成本限制 | 5 到 20 |
| 多样性很重要 | 至少为 `top_k` 的 3 到 5 倍 | 目标展示数量 |

不要把 `top_k` 当成召回窗口。`top_k` 太小会让 reranker 和 diversifier 没有足够候选可调整。

## 10. 组件选择速查

| 需求 | 推荐组件 |
| --- | --- |
| 候选只有一段文本 | `BM25Scorer + WeightedRrfFusion` |
| 标题命中要强加权 | `FieldedBM25Scorer` |
| 保留上游排序影响 | `PriorRankScorer` |
| 专有名词/错误码/接口名必须命中 | `KeywordScorer` |
| 同义表达、语义召回 | `DenseVectorScorer` |
| 多路分数尺度不同 | `WeightedRrfFusion` |
| 本地英文/通用 cross encoder 精排 | `CrossEncoderReranker` |
| 中文/中英混合精排 | `BgeReranker` |
| 外部 API 精排 | `ZeroEntropyReranker` |
| 同文档 chunk 霸榜 | `GroupRoundRobinDiversifier` |
| 文本重复/近重复 | `MmrDiversifier` |
| embedding 语义重复 | `MaxMinDiversifier` |

## 11. 常见组装原则

1. 先做可解释的轻量初排，再加模型精排。
2. scorer 尽量互补，不要堆一堆表达同一件事的信号。
3. 多字段文档优先用 `FieldedBM25Scorer`，纯文本再用 `BM25Scorer`。
4. 有上游可信排序时加 `PriorRankScorer`，否则不要硬填 `prior_rank`。
5. 有 reranker 时使用 `rank_async()`，并控制 `candidate_limit`。
6. 多样化放在 reranker 后面更符合最终展示目标。
7. `group_key` 应该在候选转换阶段就填好，后面不要靠 metadata 猜。
8. 线上 pipeline 应为 reranker 设置失败降级策略，或在调用层捕获异常。
9. 如果字段没有内容，对应 scorer 会自然跳过；不需要造空字符串以外的特殊值。
10. 调参先调 scorer 权重和字段权重，再调 reranker，最后调 diversifier。

## 12. 一个完整运行示例

```python
from ranking_engine.core import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
    RankingPipeline,
)
from ranking_engine.diversifiers import GroupRoundRobinDiversifier
from ranking_engine.fusion import WeightedRrfFusion
from ranking_engine.scorers import FieldedBM25Scorer, PriorRankScorer
from ranking_engine.text import RankingTokenizer

tokenizer = RankingTokenizer()

pipeline = RankingPipeline(
    name="demo.fielded",
    scorers=(
        FieldedBM25Scorer(tokenizer=tokenizer),
        PriorRankScorer(),
    ),
    fusion=WeightedRrfFusion(),
    diversifier=GroupRoundRobinDiversifier(),
)

engine = RankingEngine(pipeline=pipeline)

request = RankRequest(
    query=RankQuery(text="如何组装 ranking engine"),
    candidates=(
        RankCandidate(
            candidate_id="a",
            text="Ranking Engine 由 scorer、fusion、reranker、diversifier 组成。",
            fields={"title": "Ranking Engine 说明", "body": "如何组装排序流水线"},
            prior_rank=1,
            group_key="doc-1",
        ),
        RankCandidate(
            candidate_id="b",
            text="Tokenizer 支持中文分词、领域词和 bigram。",
            fields={"title": "Tokenizer", "body": "分词配置说明"},
            prior_rank=2,
            group_key="doc-2",
        ),
    ),
    top_k=5,
    candidate_limit=50,
)

result = engine.rank(request)
```

如果 pipeline 中包含 `BgeReranker`、`CrossEncoderReranker` 或 `ZeroEntropyReranker`，改用：

```python
result = await engine.rank_async(request)
```
