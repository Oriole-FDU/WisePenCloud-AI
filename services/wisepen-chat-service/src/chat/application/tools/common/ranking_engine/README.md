# Ranking Engine 说明文档

## 下游对接协议

Ranking Engine 的组件之间存在隐含的数据依赖。装配特定组件时，上游必须提供对应的字段或 metadata，否则运行时报错或静默降级。

### 硬性依赖（缺失即报错）

| 组件 | 依赖字段 | 所在对象 | 说明 |
|------|----------|----------|------|
| `DenseVectorScorer` | `metadata["embedding"]` | `RankQuery` + `RankCandidate` | 向量嵌入，缺失抛 `ValueError` |
| `KeywordScorer` | `metadata["keywords"]` | `RankQuery` | 关键词列表（list/tuple），缺失抛 `ValueError` |

### 软性依赖（缺失降级）

| 组件 | 依赖字段 | 所在对象 | 降级行为 |
|------|----------|----------|----------|
| `MaxMinDiversifier` | `metadata["embedding"]` | `RankCandidate` | 不可用时 fallback 到文本 Jaccard 相似度 |
| `GroupRoundRobinDiversifier` | `metadata[config.metadata_group_key]` | `RankCandidate` | 仅在 `group_key` 为空且配置了 `metadata_group_key` 时读取，不可用则无分组 |
| `MmrDiversifier` | `group_key` | `RankCandidate` | 缺失则不启用同组抑制 |

### fields 字段权重约定

`FieldedBM25Scorer` 和 `KeywordScorer` 默认配置了以下 fields 权重：

| fields key | 默认权重 | 说明 |
|------------|----------|------|
| `"title"` | 3.0 | 标题，最高权重 |
| `"heading"` | 2.0 | 小标题 |
| `"summary"` | 1.5 | 摘要 |

fields 为可选字段，缺失时对应字段不参与打分，但提供后可显著提升召回质量。

### 候选文本 fallback 约定

| 组件 | `text` 为空时行为 |
|------|-------------------|
| `BgeReranker` / `CrossEncoderReranker` | fallback 到拼接 `fields` 所有值 |
| `ZeroEntropyReranker` | **无 fallback**，直接传空字符串给 API，行为不可预测 |

因此，装配 `ZeroEntropyReranker` 时，**必须确保 `candidate.text` 非空**。

### 装配检查清单

| 装配组件 | 必须确保 |
|----------|----------|
| `DenseVectorScorer` | query 和每个 candidate 的 `metadata["embedding"]` 已填充 |
| `KeywordScorer` | query 的 `metadata["keywords"]` 已填充（list/tuple） |
| `FieldedBM25Scorer` | candidate 的 `fields` 至少包含 `"title"` |
| `PriorRankScorer` | candidate 的 `prior_rank` 已填充（None 时跳过） |
| `MaxMinDiversifier`（embedding 模式） | candidate 的 `metadata["embedding"]` 已填充 |
| `GroupRoundRobinDiversifier` | candidate 的 `group_key` 或 `metadata[metadata_group_key]` 已填充 |
| `ZeroEntropyReranker` | candidate 的 `text` 非空 |
