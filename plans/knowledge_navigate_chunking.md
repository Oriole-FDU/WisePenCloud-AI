# 结构优先的 Chunk 系统改革

## 结论

当前 chunk 系统需要直接替换，不能只增加 `section_id`。

现有主流程是：

```text
Markdown blocks
  -> 按 6000 字符聚合 parent
  -> 在 parent 文本上按 600 字符递归切 child
  -> 合并孤立标题和短尾
```

Markdown 结构只影响分隔符和 metadata，真正决定结果的仍是长度。parent 也只是较大的长度块，不是文档中的真实父节点。这会带来四个直接问题：

- Section、页、表格等结构只能通过 chunk 反推；
- child 二次解析重建文本，丢失原始 block 归属和标题路径；
- normalization 在结构切分完成后重新改变边界；
- 检索文本、证据原文和回答上下文被迫共用同一个 `Chunk.text`。

新流程改为：

```text
Kafka Markdown
  -> source-backed blocks
  -> Section/Page/Anchor 结构投影
  -> paginated / flowing policy
  -> 结构优先、长度受限的 RetrievalChunk
  -> index serialization

RAG hit
  -> SourceSpan
  -> SectionView / PageView
  -> 按回答预算展开上下文
```

长度只约束检索和模型输入，不再定义文档结构。

## 社区实践对应

- [Docling HybridChunker](https://docling-project.github.io/docling/concepts/chunking/) 先按文档层级产生结构块，再只对超出
  token 上限的块细分；只有 heading/caption 相同的 peer 才可合并。它还把原文 `chunk.text` 与用于 embedding 的
  `contextualize(chunk)` 分开。
- [Unstructured `by_title`](https://docs.unstructured.io/open-source/core-functionality/chunking/) 区分 soft max 与 hard
  max，优先组合完整 element；单个 element 超过 hard max 时才做文本切分。overlap 默认只用于被迫拆开的 oversized element。
- [LlamaIndex AutoMergingRetriever](https://docs.llamaindex.ai/en/v0.10.17/examples/retrievers/auto_merging_retriever.html)
  只索引 leaf，多个 leaf 命中同一 parent 时再提升为 parent 上下文，说明召回粒度与阅读粒度应分离。
- [Haystack DocumentSplitter](https://docs.haystack.deepset.ai/docs/documentsplitter) 提供 paragraph、sentence、page
  等切分单位和短尾阈值；它证明短尾合并是一种可选策略，而不是结构解析后的必经 normalization。

WisePen 已有 `markdown-it-py` 产生的原文 block，也已有 `docling` 依赖，但 Kafka 输入已经是 Markdown。这里参考 Docling 的
hybrid 设计，不把 Kafka 正文重新接入 chat `document_parse` 或转换成 `DoclingDocument`。

本次实现另外检出了 Unstructured `d309caf8ee20b735eb105d4e16ac3f04e5a48172`，实际蒸馏的是
`boundary predicates -> pre-chunk -> oversized split` 三段式处理；没有复制它的动态 registry。

## 当前底层 API

```python
MarkdownChunkerConfig(
    strategy=MarkdownChunkingStrategy.AUTO,
    max_characters=6000,
    new_after_n_chars=None,
)
```

- `AUTO`：存在 page marker 时解析为 `BY_PAGE`，否则解析为 `BY_TITLE`；实际策略写入 `ChunkingResult.metadata["strategy"]`。
- `BY_PAGE`：正常情况一页一个 chunk，只有整页超过 `max_characters` 才在页内按 block fallback。
- `BY_TITLE`：标题开始新的语义单元，允许同一 Section 跨页；连续空标题层级与首个正文一起进入 pre-chunk。
- 每个 `Chunk` 保存真实 `source_spans`。Tool 侧同步使用 plural `section_paths/page_labels`，按 spans 回读证据。
- 通用层的 `ParentChildMarkdownChunker`、`ChunkRole`、heading/短尾 normalization 已删除。

hard/soft limit 明确按字符计数，足以支撑 Tool Content 的存储与窗口读取。RAG 如需模型相关预算，应在 `application/rag` 自己的
projection 和上下文组装阶段处理。

## 三层产物

### 1. 文档结构

Parser 只负责识别有原文 offset 的结构事实：

```text
TextBlock
  block_kind
  start_offset / end_offset
  section_path
  heading_level
  page_label
```

由 blocks 一次构造：

- `SectionNode`：完整逻辑章节、父子关系、直接正文范围和 subtree 范围；
- `PageSpan`：物理页范围；
- `AnchorSpan`：表格、公式、图片等可定位对象。

这些结构都直接指向 Kafka 原文，不依赖 chunk 边界存在。

### 2. 检索单元

应用到 RAG 时，`Chunk` 投影为只承担召回的 `RetrievalChunk`：

```text
chunk_id
reading_block_id
section_id
source_spans
block_kinds
raw_text
index_text
```

- `source_spans` 是权威映射；不能再假设一个 chunk 只由一个连续 `start_offset/end_offset` 完整表达。
- `raw_text` 已能由 spans 从 Kafka 正文确定性物化，用于 evidence。
- `index_text` 由后续 RAG projection 将标题路径、必要 caption 与 `raw_text` 序列化，不进入底层 chunk 证据模型。
- RAG 应用层的 RetrievalChunk 只属于一个 ReadingBlock 和一个 Section；底层通用 page leaf 若覆盖多个 Section，
  由 Section projector 按 source spans 重新投影，不把 page leaf 直接当成 RAG 语义节点。
- 一个超长 Section 可以对应多个 RetrievalChunk；Section 本身仍可通过自己的原文范围完整读取。

### 3. 阅读上下文

`SectionView` 和 `PageView` 是应用层读取结果，不是索引 chunk：

```text
SectionView { path, content, source_ref }
PageView    { page_label, content, source_ref }
```

命中后再决定读取粒度：完整 Section 放得下就直接返回；放不下则返回命中片段、同 Section 相邻片段、祖先标题和子章节入口。多个命中片段属于同一
Section 时合并提升，避免把重复标题路径和重叠正文多次塞进上下文。

## 两种文档策略

chunker 先根据正文中是否存在可信 page marker 选择策略，不用同一条规则覆盖 PDF 和普通 Markdown。

### Paginated：一页一个 chunk

带 `<!-- page N -->` 的正文默认把一页作为一个 retrieval chunk。page 已经是明确的物理边界，常见 PDF
页通常也能落入模型预算；整页保留版面邻近关系，并让 chunk、page locator 和 citation 使用同一范围。

```text
PageSpan <= max_characters
  -> one RetrievalChunk

PageSpan > max_characters
  -> 仅在该页内按 Section/block 边界拆分
  -> 单个 oversized block 再使用通用 Markdown 递归回退
```

- 不跨页合并短尾，也不因为页内出现标题就强制拆页。
- 底层 page chunk 可以包含多个 Section，保存每个 Section 在本页的独立 span；RAG projector 再按 Section 生成检索叶子。
- 一个 Section 可以跨多个 page chunks，`SectionView` 按 Section 自身范围跨页重建。
- `index_text` 写入本页覆盖的标题路径；`raw_text` 只取本页正文。
- hard limit 是异常保护，不把“任何 PDF 页都一定足够短”写成系统假设。

这比当前“page 是硬边界，但 page 内继续按 6000 字符任意聚合”更直接。[Unstructured
`by_page`](https://docs.unstructured.io/concepts/chunking/) 同样把 page 作为不可跨边界，并保留 hard max 处理异常超长页；WisePen
在常规页内进一步选择整页作为默认 leaf。

### Flowing：Section/block 优先

没有 page marker 的 Markdown、笔记和普通文本不存在稳定页概念，按逻辑结构装箱：

1. 未超过 soft limit 的 Section 保持完整。
2. 相邻短 Section 属于同一父节点且合并后不超过 soft limit 时，底层 chunk 可装箱，但不合并 Section 身份；RAG projector
   仍按 Section 生成 ReadingBlock。
3. 超长 Section 在内部按完整 block 装箱，允许最后一组达到字符硬上限。
4. 单个 block 超过硬上限时使用通用 Markdown 递归回退。

两种策略输出相同的 `RetrievalChunk + source_spans`，RAG 的 SectionNavigator 不感知输入最初是不是 PDF。

## Flowing 文档的结构优先装箱

### 处理顺序

```text
完整 Section
  -> Section 内完整 block
  -> 通用 Markdown separator 递归回退
```

`soft_limit` 控制期望块大小，`hard_limit` 控制单个 chunk 的字符上限。正常结构块不拆；只有单个 block 已超过 hard limit
时才进入递归回退。

### 超长结构块

所有 oversized block 使用同一套 Markdown separator 回退。通用 Tool Content 只维护这一条稳定的回退路径。

正常相邻 chunk 和 oversized fallback 都不使用 overlap，避免重复证据和 offset 歧义。

## Page 策略验证

`one page = one chunk` 作为 paginated 文档的默认实现，但仍与页内 leaf 做一次对比：

| 方案                | 检索 chunk               | 读取上下文                       |
|-------------------|------------------------|-----------------------------|
| page leaf         | 正常情况一页一个 leaf          | PageView 或 SectionView      |
| page-bounded leaf | 页内按 Section/block 继续拆分 | SectionView + page citation |

如果两者 Recall@k 接近，就采用更简单的一页一块；如果一页多主题导致向量召回明显下降，再保留页内结构 leaf。两种方案都不允许跨页
chunk。

## 删除 normalization 修补

`merge_heading_only()` 和 `merge_short_tails()` 不再位于生产路径。需要的行为放回构造阶段：

- heading 天然属于它创建的 Section；
- caption 在 parser 阶段绑定表格；
- 短 Section 由 packer 按同父、相邻、完整保留的条件装箱；
- 最后一个短 fragment 是否并入前块，由 soft/hard limit 和结构类型当场决定。

这样 packer 一次确定最终 spans，不再产生 ID remap，也不会在 child 已生成后改 parent 边界。

## 通用层不承载 parent-child

`ParentChildMarkdownChunker` 的 arbitrary parent 不再保留。新的层级是：

```text
SectionNode / PageSpan          真实结构与可读取 parent
  -> RetrievalChunk            Qdrant dense/sparse leaf
```

短 Section 也始终产生 retrieval leaf。RAG 只索引 leaf；回答时通过 leaf 的 spans 回到 SectionView。RAG 若需要父子召回或命中提升，直接在
`application/rag` 基于这些 source-backed chunks 实现，本轮不在通用 chunker 中预留接口。

`PlainTextChunker` 保留无结构 fallback，按 paragraph、sentence、character 逐级切分；整篇文本作为 `document_root`，因此
RAG 不需要第二套消费协议。

## 实施 TODO

- [x] 增加 `AUTO/BY_PAGE/BY_TITLE` 显式策略。
- [x] paginated 正常页一页一块，超长页只在页内 fallback。
- [x] `BY_TITLE` 按标题语义单元构造 pre-chunk，并允许 Section 跨页。
- [x] parser 输出 `heading_level/page_label`，chunk 和 locator 使用 source spans。
- [x] ToolContentStore/reader 迁移到 plural locators 与 source spans。
- [x] 删除通用层 arbitrary parent-child 与事后 normalization。
- [x] 使用字符 soft/hard limit；超长 block 统一走 Markdown separator fallback。
- [ ] RAG projection 生成独立 `index_text`，并实现 SectionTree/SectionView。
- [ ] 运行 page leaf 与 page-bounded leaf 的离线召回对比。

离线测试语料必须包含：大量短标题、单个超长 Section、跨三页 Section、一页多个 Section、超长表格/列表、无标题正文。

比较：

```text
A. 改造前 length parent/child + normalization 基线
B. structure-first leaf
C. B + 同 Section 命中提升
D. paginated: page leaf vs page-bounded leaf
```

记录：gold evidence Recall@k、完整 Section 重建率、无关上下文量、page citation 准确率、超长结构完整率、P50/P95
延迟和索引量。默认字符参数由实验选定。

## 完成标准

- 每个 retrieval chunk 的 evidence 都能通过 `source_spans` 精确回到 Kafka 正文；
- 任意 Section 的完整读取不依赖它被切成几个 chunk；
- 正常 paginated 文档一页一个 chunk，超长页的 fallback chunks 仍严格留在本页；
- 正常 chunk 不拆分完整 Markdown block；
- 超长结构块始终受字符 hard limit 约束；
- 短 Section 不因 merge 丢失独立 path、range 或 locator；
- index enrichment 不污染 evidence 原文；
- 更新一个 Section 时，能按 source/section spans 判定需要重建的 leaf 和上下文。
