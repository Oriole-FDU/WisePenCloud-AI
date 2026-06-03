# 算法模块

## 概览

本目录提供应用层共享的确定性算法，涵盖哈希、分词、排名融合三类基础设施。

```
algorithms/
├── hash.py             # 稳定哈希
└── ranking/
    ├── tokenizer.py    # 中英混合分词
    ├── bm25.py         # BM25 关键词检索 + LRU 缓存
    ├── fielded_bm25.py # 多字段加权 BM25
    ├── rrf.py          # 倒数排名融合 (RRF)
    └── mmr.py          # 最大边际相关性 (MMR) 多样性去重
```

## 数据流

```
原始文本
  │
  ├── tokenizer.py ──→ tokenize_for_bm25 ──→ token 列表
  │                                              │
  │    ┌─────────────────────────────────────────┤
  │    ▼                                         ▼
  │ bm25.py ──→ rank_documents_by_bm25     mmr.py ──→ select_by_mmr
  │              │ (带 LRU 缓存)                      │ (Jaccard 相似度)
  │              ▼                                    ▼
  │         Bm25RankResult                    MmrSelectedItem[]
  │              │
  ├── fielded_bm25.py ──→ rank_fielded_bm25
  │                        │ (多字段加权累加)
  │                        ▼
  │                   文档 ID 排序列表
  │                        │
  ├── rrf.py ──→ weighted_rrf ──→ 多路融合排序列表
  │                                 │
  ▼                                 ▼
hash.py ──→ stable_hash / stable_hash_json
            (SHA256, 16 字符 hex)
```

---

## hash.py — 稳定哈希

| 函数 | 用途 | 调用方 |
|------|------|--------|
| `stable_hash(value: str) -> str` | SHA256 取前 16 位 hex，用于 URL/ID 标准化 | `SearchResultCandidate.id` |
| `stable_hash_json(value: object) -> str` | JSON 序列化后 SHA256，keys 排序保证确定性 | 通用 |

---

## tokenizer.py — BM25 通用分词器

### `tokenize_for_bm25(text: str) -> List[str]`

中英混合分词，为 BM25 提供词袋输入。

**策略**：

| 文本类型 | 处理方式 |
|----------|----------|
| 英文/数字 token | 正则抽取 → 整体保留 + 连接符分片 + CamelCase 拆分 → casefold |
| 中文连续片段 | jieba `cut_for_search` 搜索引擎模式 |
| 停用词 | 中英文通用停用词表过滤 |
| 去重 | 每个 token 只保留首次出现 |

**正则常量**：

| 常量 | 匹配内容 | 作用 |
|------|----------|------|
| `RE_CJK` | 中文字符连续片段 | 交由 jieba 分词 |
| `RE_ALNUM_TOKEN` | `A-Za-z0-9` 及 `._-` 连接的复合词 | 英文/数字 token 抽取 |
| `RE_CAMEL_BOUNDARY` | CamelCase 边界 | 拆分为子 token |

---

## bm25.py — BM25 关键词检索

### `rank_documents_by_bm25(query, documents, cache_key=None) -> Bm25RankResult`

标准 BM25 关键词检索，支持 LRU 索引缓存。

**流程**：
1. 对 query 和每篇文档调用 `tokenize_for_bm25`
2. 构建 BM25 倒排索引（命中缓存则跳过）
3. 批量算分 → 按分数降序排序
4. 返回 `Bm25RankResult`（含耗时、缓存命中状态）

**缓存机制**：
- 以 `cache_key` + SHA256 数据指纹为 key
- 最大缓存 **32** 个索引（`BM25_INDEX_CACHE_MAXSIZE`）
- 线程安全的 LRU 淘汰策略

**特殊路径**：
- 单文档：跳过索引构建，直接算 Jaccard-like 分数
- 空 query：均分 0.0 分，保持相对顺序

---

## fielded_bm25.py — 多字段加权 BM25

### `rank_fielded_bm25(query, items, field_weights) -> List[str]`

对带多字段的文档进行加权 BM25 排序，常用于搜索结果的 title/snippet/url 组合评分。

**流程**：
1. 遍历每个字段，独立调用 `rank_documents_by_bm25`
2. 每字段结果 × `field_weights[field]` 累加
3. 按总分降序输出文档 ID 列表

**缓存**：每个字段使用 `fielded_bm25:{field_name}` 作为 `cache_key`，复用 `bm25.py` 的 LRU 缓存。

**典型调用**（`result_ranking.py`）：

```python
rank_fielded_bm25(
    query,
    metadata_items,
    {"title": 2.0, "snippet": 1.0, "url_path": 0.3},
)
```

---

## rrf.py — 倒数排名融合 (RRF)

### `weighted_rrf(ranked_lists, k=60) -> List[RrfRankedItem]`

多路召回结果的无损加权融合算法。不需要原始分数，仅依赖排名位置。

**公式**：

```
Score(id) = Σ weight_i / (k + rank_i + 1)
```

- `k = 60`：平滑常数，防止第一名权重过高
- 分数持平按首次出现顺序（稳定排序）
- 自动记录每个 ID 的来源渠道（`sources`）

**典型调用**（`result_ranking.py`）：

```python
weighted_rrf(
    [
        RankedList(name="source_original", ids=source_ids, weight=1.0),
        RankedList(name="metadata_bm25", ids=bm25_ids, weight=1.0),
    ],
    k=60,
)
```

---

## mmr.py — 最大边际相关性

### `select_by_mmr(candidates, top_k, lambda_mult=0.72, same_group_similarity=0.92) -> List[MmrSelectedItem]`

贪心选择最优候选集，平衡相关性与多样性。

**公式**：

```
MMR(id) = λ * relevance(id) - (1-λ) * max_similarity(id, selected)
```

**参数**：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `lambda_mult` | 0.72 | 相关性权重（越大越偏重相关性，越小越偏重多样性） |
| `same_group_similarity` | 0.92 | 同组候选的强制相似度下限（防同一资源霸榜） |

**相似度计算**：Jaccard 相似度（基于 `tokenize_for_bm25` 分词后的 token 集合）

**输入要求**：`MmrCandidate.relevance_score` 需预先归一化到 0-1。

---

## 维护说明

- **新增算法**：在 `ranking/` 下新建文件，更新本文档的概览和数据流图。
- **修改参数**：默认值变更需同步更新本文档的表格，并在调用方验证兼容性。
- **添加调用方**：在数据流图中补充箭头。
- **分词器变更**：`tokenize_for_bm25` 是所有排序算法的上游，修改停用词或分词逻辑需回归测试 `bm25.py` 和 `mmr.py`。
