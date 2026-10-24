# Docling Hybrid Chunking 源码级调研

日期：2026-10-08
范围：源码事实、当前本地安装行为、WisePen 当前调用链对照。本文只新增报告，不修改现有解析器、Chunker、RAG、Chat、依赖或锁文件。

## 结论先行

主推荐是 **C：维护 WisePen Node IR，局部参考/复用 tokenizer、serializer 和切分算法**。Docling 的主要价值是把“结构分组、token 预算、序列化、超限回退、同元数据合并”拆成清晰阶段；它不适合作为 WisePen 的强制统一 IR，因为 Markdown backend 会归一化 Markdown，`SerializationResult` 保存的是 DocItem 引用而不是 Markdown 字符区间，默认表格序列化还会把表格改写成检索友好的 triplet 文本。WisePen 当前已经有 `DocumentNode`、`Section`、`SourceSpan` 和 RAG/Chat 的精确回源合同，直接替换为 `DoclingDocument` 会丢失这些语义边界。

建议接受 Docling 的结构感知和 token-aware 思路，但不接受其 Markdown normalization 作为 WisePen 的原文事实；保留 WisePen 自有 table、code、list、formula 语义。若未来只需要 token-aware 普通文本切分，可以直接评估 `semchunk`，不必引入完整 Docling。

## 1. 事实来源和版本边界

### 1.1 源码基准

本报告追踪 GitHub `main` 的浅克隆：

| 仓库 | commit | 关键源码 |
|---|---|---|
| `docling` | `213288f4a81bc1f509522a854c0c807e96dbc5a3` | [Markdown backend](https://github.com/docling-project/docling/blob/213288f4a81bc1f509522a854c0c807e96dbc5a3/docling/backend/md_backend.py) |
| `docling-core` | `2afbea50cf13a8ab12aec1afe844991882b12054` | [HybridChunker](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/docling_core/transforms/chunker/hybrid_chunker.py) |

源码行号以下面的 commit URL 为基准；GitHub `main` 后续改变时，应以 commit 链接为准。

官方概念和测试也作为交叉核对来源：[chunking concepts](https://github.com/docling-project/docling/blob/213288f4a81bc1f509522a854c0c807e96dbc5a3/docs/concepts/chunking.md)、[HybridChunker tests](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/tests/test_hybrid_chunker.py)、[HierarchicalChunker tests](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/tests/test_hierarchical_chunker.py) 和 [hybrid chunking example](https://github.com/docling-project/docling/blob/213288f4a81bc1f509522a854c0c807e96dbc5a3/docs/examples/hybrid_chunking.ipynb)。

### 1.2 本地实验版本

实验机上已安装：

| 包 | 版本 | 用途 |
|---|---:|---|
| `docling` | 2.111.0 | Markdown 转换和 `DocumentConverter` |
| `docling-core` | 2.86.0 | `HybridChunker`、serializer、tokenizer |
| `semchunk` | 3.2.5 | Hybrid 的 plain-text fallback 依赖 |
| `transformers` | 5.13.0 | HuggingFace tokenizer 依赖路径 |
| `tiktoken` | 0.12.0 | 实验显式使用 `cl100k_base` |
| `tree-sitter` | 0.26.0 | docling-core chunking extra 的代码切分基础 |

因此，实验结果是 **2.111.0/2.86.0 的可复现行为**，不是对当前 `main` 运行结果的保证。当前 `docling` main 的 pyproject 版本为 2.135.0，并要求 `docling-core>=2.98,<3`；这正是源码和本地行为必须分开记录的原因。

### 1.3 实验范围

临时目录：

`%TEMP%\wisepen-docling-study-5b400cb18ed9443682cf54010e8f7f6c\`

其中包含源码浅克隆、fixture、行为输出和一次独立 code fence 输出；未加入仓库。实验使用显式 `OpenAITokenizer(tiktoken.get_encoding("cl100k_base"), max_tokens=64/128)`，避免 HuggingFace 模型下载影响结果。

## 2. WisePen 当前真实调用链

本节代码行为是调研时记录的基线；后续重构调整了包目录与分块职责，以下路径按当前模块位置更新。

当前代码事实如下：

~~~text
上游 Markdown
  → common DocumentParser(markdown-it-py + dollarmath + figure plugin)
  → DocumentNode tree + Section
  → common DocumentChunker
  → RAG DocumentPreparer.prepare(markdown)
  → DocChunk / Mongo / Qdrant
  → retrieval、contextualization、GraphFactBuilder
~~~

- `DocumentParser` 在 `services/wisepen-common/src/common/utils/markdown/parsing/parser.py`，由 `parsing/__init__.py` 导出，直接消费 Markdown 并生成 `DocumentNode`。
- Node、Chunk、Section 和 OutlineNode 分别定义在其首次产出模块；原文范围类型 `SourceSpan` 与解析节点定义在 `parsing/parser.py`。
- 当前 Chunking 模块位于 `services/wisepen-common/src/common/utils/markdown/chunking/`，共享配置为 `target_chunk_tokens=800`、`split_threshold_tokens=1600`。
- StructuralNodeSplitter 与 ChunkPacker 是独立阶段，分别位于 `chunking/splitter.py` 与 `chunking/packer.py`；DocumentChunker 负责编排。
- `DocumentPreparer` 位于 `services/wisepen-rag-service/src/rag/application/document/preparation.py:30-50`，直接接受 Markdown，构造 `soft_limit=800`、`hard_limit=1600`、`chunk_overlap=100` 的 Common Chunker。
- RAG 的 Mongo 投影在 `services/wisepen-rag-service/src/rag/core/persistence/mongo/doc_chunk_repository.py:118-135`，持久化 `source_spans`；`DocChunk` 在 `services/wisepen-rag-service/src/rag/application/document/models.py:117-151` 强制要求至少一个 span，并由 span 计算 `chunk_span`。
- `build_inline_document_context` 在 `services/wisepen-rag-service/src/rag/application/document/context.py:13-123` 使用 target 的精确 spans 扣除原文上下文；它特别避免用 envelope 把 target span 之间的空洞重新注入。
- Indexing 在 `services/wisepen-rag-service/src/rag/application/document/indexing.py:226` 调用该 context builder；GraphFactBuilder 也复用同一逻辑。
- Chat 缓存使用 Common chunk 的 source spans；`cache_store.py:23-48` 保留 RAG/Chat 的 800/1600/100 配置，窗口读取在 `window.py:66-101` 按多个 SourceSpan 计算。
- Chat 网页 Markdown 来自 `trafilatura.extract(..., output_format="markdown", include_tables=True, include_links=True)`，见 `services/wisepen-chat-service/src/chat/application/tools/web_tools/page_content.py:7-58`；PDF 路径调用 `pdf_inspector.extract_pages_markdown_bytes` 并连接 page Markdown，见同文件 `:90-100`。
- `mineru==3.4.4` 只在 `services/wisepen-chat-service/pyproject.toml:38` 出现；本次对 `services/*/src` 的检索没有发现运行时 MinerU import，因此不能把 MinerU 当作当前 ingestion 链路的事实。

### 2.1 哪些地方真正依赖精确 span

真正依赖精确 span 的是：

1. RAG/Chat 对原始 Markdown 做 inline contextualization 时的 target 扣除和相邻窗口计算；
2. Section/Anchor/Range 读取时，把 chunk 或 section 映射回原文；
3. Mongo `DocChunk` 持久化和读取后重建；
4. GraphFactBuilder 生成 target-only 上下文；
5. 用户可见的原文引用、范围读取和多 span table chunk。

只需要 envelope 的是：

1. 以连续区间近似检索窗口的 retrieval 组装（例如 `hybrid_retriever.py:449-450`）；
2. 排序、向量召回和 chunk ID；
3. 只显示 chunk 自身文本而不回源的场景。

这一区分支持“chunk text 可以被 serializer 合成，但 provenance 仍单独保存”的设计；它也说明 Docling 的 DocItem identity 不能直接替代 WisePen 的字符 span 合同。

## 3. Docling 源码追踪

### 3.1 HybridChunker 的真实流程

`HybridChunker` 位于 [hybrid_chunker.py](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/docling_core/transforms/chunker/hybrid_chunker.py)：

- `HybridChunker` 类在 `:55`，`_inner_chunker` 在 `:127`，返回 `HierarchicalChunker`。当前 main 的 Hybrid 没有 `code_chunking_strategy` 字段，也没有把代码专用 splitter 传入 inner chunker。
- `_count_text_tokens` 在 `:133`，`_count_chunk_tokens` 在 `:148`，`_doc_chunk_length` 在 `:152`。计数调用显式 tokenizer；`contextualize()` 产生的 headings/captions 也计入预算，除非 metadata 标记 `excluded_embed`。
- `_split_by_doc_items` 在 `:161`。它先把相邻 DocItem 序列化为候选 `DocChunk`，计算 delimiter 和 metadata overhead，在 `max_tokens` 内贪心装箱；发生 overshoot 时重新计数并收缩到最大可放窗口。单个 DocItem 本身超过预算时会原样进入 plain-text stage。
- `meta_overhead` 的计算在 `:219`；当 metadata 本身太大，`_split_using_plain_text` 会警告并移除 captions/headings 后递归。
- `_split_using_plain_text` 在 `:253`，先计算 contextualized text 的 token 数，再把 metadata overhead 从 `max_tokens` 中扣除，最后调用 `segment`。
- `segment` 在 `:281`。普通文本调用 `semchunk.chunkerify(tokenizer, chunk_size=available_length)`，当前调用不传 `offsets` 和 `overlap`；表格在满足条件时改走 `LineBasedTokenChunker`。
- `_merge_chunks_with_matching_metadata` 在 `:344`，只合并连续且 headings 相同的 DocChunk，并再次计算 contextualized token 数；合并使用 delimiter 拼接 text 和 doc_items。
- public `chunk` 在 `:390`，顺序就是 `HierarchicalChunker → _split_by_doc_items → _split_using_plain_text → optional merge`。

不超过 15 行的等价伪代码：

~~~text
inner = HierarchicalChunker(serializer_provider=provider)
for hierarchical_chunk in inner.chunk(document):
    windows = split_by_doc_items(hierarchical_chunk, max_tokens)
    for window in windows:
        budget = max_tokens - token_count(metadata(window))
        if token_count(contextualize(window)) <= max_tokens:
            yield window
        else:
            for text in segment(window.text, budget):
                yield DocChunk(text=text, meta=window.meta)
merge_adjacent_chunks_with_same_headings()
~~~

### 3.2 HierarchicalChunker、identity 和 serializer

`HierarchicalChunker` 位于 [hierarchical_chunker.py](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/docling_core/transforms/chunker/hierarchical_chunker.py) `:170-192`。

- 它遍历 `DoclingDocument.iterate_items(with_groups=True)`，维护 heading level stack；heading 默认放在 metadata，不进入 chunk text。
- `visited` 集合交给 serializer，避免 group/child 重复序列化。
- 输出接收 `ListGroup | InlineGroup | DocItem`。每个 `DocChunk.meta.doc_items` 保存 DocItem 的结构 identity/ref；不是 Markdown 字符 offset。
- `ChunkingDocSerializer` 使用 Markdown 参数，默认表格 serializer 是 `TripletTableSerializer`。`TripletTableSerializer` 在 `:46` 把多列表格改成 `row, col = value` triplet 文本；它保留的是 TableItem 引用。
- `SerializationResult` 和 `Span` 在 [serializer/base.py](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/docling_core/transforms/serializer/base.py) `:25-44`。`Span` 只有 `item: DocItem`，`SerializationResult` 只有 `text` 和 `spans`，没有源 Markdown 的 start/end offset。
- Markdown 表格 serializer 在 [serializer/markdown.py](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/docling_core/transforms/serializer/markdown.py) `:695-712`，可提供 header/body 行供 line chunker 重复 header；这不是默认 Triplet serializer 的行为。

### 3.3 LineBasedTokenChunker 和 semchunk

`LineBasedTokenChunker` 位于 [line_chunker.py](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/docling_core/transforms/chunker/line_chunker.py) `:20`。它优先保持换行；单行超过 token limit 时在 `:284` 用二分查找字符边界，尽量靠近单词边界。它不是 AST-aware code parser。

`semchunk` 的独立源码行为是：

- `chunkerify` 支持 `chunk_size`、可选 `offsets`、可选 `overlap`；
- 递归使用换行、制表、空白和语义标点作为分隔符；
- `offsets=True` 可以返回每个 chunk 的原文字符区间；
- `overlap` 只有显式传入才启用；
- Docling main 的 Hybrid 调用没有传 `offsets` 或 overlap，因此默认结果不携带字符 offset，也不产生 overlap。

这属于 **parser-aware + structure-aware + token-aware** 的组合；`semchunk` 本身是启发式 separator splitting，不是 embedding/LLM semantic chunking。Docling 当前 Hybrid 没有调用语义模型。

### 3.4 code、formula 与 fallback

代码专用策略位于 [standard_code_chunking_strategy.py](https://github.com/docling-project/docling-core/blob/2afbea50cf13a8ab12aec1afe844991882b12054/docling_core/transforms/chunker/code_chunking/standard_code_chunking_strategy.py) `:38-57`，但 Hybrid 当前没有传 `code_chunking_strategy`，所以默认路径不会自动使用 tree-sitter 代码切分。

Docling 的 `FormulaItem` 在通用 Markdown backend 里没有与 WisePen `$...$` 语法一一对应的专门 fallback。Markdown backend 的公式实验结果是普通 `TextItem`，因此 formula 没有单独 atomicity 合同。

## 4. Markdown backend 和 normalization

Markdown backend 位于 [md_backend.py](https://github.com/docling-project/docling/blob/213288f4a81bc1f509522a854c0c807e96dbc5a3/docling/backend/md_backend.py)：

- `MarkdownDocumentBackend` 在 `:164`；Marko/GFM parser 初始化在 `:1113` 左右；
- GFM table 解析在 `_parse_gfm_table`，约 `:353`，生成 `TableItem`、`TableCell`/`RichTableCell`；
- AST 迭代器 `_iterate_elements` 约 `:698`，处理 heading、list、emphasis/link/image、raw text、fenced code；
- code language 通过 `detect_code_language(snippet_text, hint=element.lang)` 推断；
- `TableCell` 保存行列索引、text 和可选 bbox，不保存原始 Markdown cell 字符 offset。

固定 fixture 的观测：

~~~~text
# heading

普通 paragraph

- list
  - nested list

> blockquote

~~~python
code
~~~

| table | x |
|---|---|

$$
formula
$$

inline formula

<div>html</div>

---
~~~~

本地 2.111.0 结果：

| Markdown 构造 | Docling 类型/输出 | 事实 |
|---|---|---|
| heading | `TitleItem`/`SectionHeaderItem` | heading 默认进 metadata，不默认进入 text |
| paragraph | `TextItem` | 保留为文本 item |
| ordered/nested list | `ListGroup`、`ListItem`、嵌套 `ListGroup` | `enumerated=True` 可见；未暴露独立 `start` 字段 |
| blockquote | `InlineGroup` + `TextItem` | quote marker 不在最终序列化文本中 |
| table | `TableItem` | 默认 Hybrid 用 triplet serialization，不保持原 Markdown 表格 |
| fenced code | `CodeItem` | 本地 2.111.0 打印 `code_language=unknown`；当前 main 源码已有 hint 推断，属于版本差异 |
| `$$...$$` | `TextItem` | 没有 formula-specific chunk strategy |
| HTML block | `TextItem` | 标签消失，只留下文本 |
| horizontal rule | 无显式 item | serializer 输出中不保留独立分隔线 |

Markdown 导出也发生 normalization：列表缩进、表格空格/对齐、代码 fence 语言信息和公式表达都可能改变；HTML 标签会被去掉。直接 backend 检查显示 Title/Text/Table 的 `prov=[]`，即 Markdown 输入没有 page/bbox/charspan provenance。

因此 Docling provenance 是“结构 item identity”，不是“原 Markdown 精确字符 span”。Docling-core issue [#738](https://github.com/docling-project/docling-core/issues/738) 也记录了序列化 table 当前以整个 TableItem 作为来源范围，cell-level tracing 尚未成为稳定合同。

## 5. 行为实验结果

实验统一输出 chunk index、text、token count、headings、captions、doc_items identity 和 contextualize(chunk)。

### 5.1 max_tokens=64

- normal fixture 得到 5 chunks；
- 多段正文、list、blockquote 按结构 item 先装箱，heading 保存在 metadata；
- table chunk 的 triplet text 约 55 tokens，code 约 34，formula/HTML 合并约 40；
- 超大 fixture 得到 66 chunks：paragraph 普遍约 54 text tokens/60 contextual tokens，长 list item 被切成多个 chunk，但各 chunk 保留同一个 ListItem self_ref；table 约 61-64 text tokens，code 约 56-57；
- 某些 table chunk contextual token 数达到 66-69，超过 64，因为 table line chunker 使用 serializer token limit，没有扣除 heading metadata overhead；
- 独立 40 行 fenced code 实验得到 14 chunks：首 chunk 有 opening fence，中间 chunk 没有 fence，最后 chunk 才有 closing fence。

### 5.2 max_tokens=128

- normal fixture 得到 3 chunks，同 heading 的相邻 chunk 会被 merge；
- 超大 fixture 得到 33 chunks：paragraph 约 117 text/123 contextual，长 list item 约 121-122/127-128，code 约 112-113/118-119；
- table contextual token 数仍可能达到 133，超过 128；
- 默认 `TripletTableSerializer` 不重复 Markdown header，因为其 `get_header_and_body_lines()` 返回空 header。只有使用提供 header/body lines 的 Markdown serializer 时，header repetition 路径才有意义。

实验结论：

1. “token-aware”不等于严格所有输出都不超 token limit，尤其是 table serializer 和 heading metadata overhead 组合；
2. 默认 Hybrid 不保证每个 oversized fenced code chunk 都包含完整 fence；
3. `DocItem` identity 在 plain-text split 后可以重复出现在多个 DocChunk，但没有字符 span；
4. 默认 Hybrid 不使用 overlap；
5. text、metadata 和 serializer 输出的 token budget 是三个不同层次，不能只看 `chunk.text`。

## 6. 特殊结构事实表

| 结构 | 正常大小 | 略微超限 | 严重超限 / fallback |
|---|---|---|---|
| paragraph | TextItem，heading 在 metadata | `_split_by_doc_items` 先装箱 | `semchunk` separator recursion；无 source offset |
| list | ListGroup/ListItem，nested list 保持 group | 先按 item 装箱 | 单个 item 进入 plain-text splitter；同一 item identity 可重复 |
| table | TableItem；默认 triplet serializer | row/item 级 packing | line-based token split；header 重复依赖 serializer；wide row 可拆成文本行，不保证 cell provenance |
| code | CodeItem，serializer 输出 code text | 仍按 DocItem | 默认 Hybrid 不启用 code strategy；plain text/line split 不保证每片完整 fence |
| formula | Markdown backend 本地为 TextItem | 普通 text 预算 | 无 formula-specific strategy |
| blockquote | InlineGroup/TextItem | 作为 item 装箱 | plain-text fallback，quote marker 可能消失 |
| HTML | 标签被 backend 丢弃 | 不作为 HTML 结构保留 | 只剩 text |
| horizontal rule | 无独立 item | 无 | 无独立 chunk 语义 |
| figure/caption | 取决于 backend DocItem/caption | caption 计入 metadata/token | metadata 过大时 Hybrid 可移除 caption/headings 后重试 |

## 7. 依赖和成熟开源实现对照

Docling main 的依赖边界：

~~~text
docling
  → docling-core[chunking]
      → semchunk>=2.2,<4
      → transformers>=4.42,<6
      → tree-sitter>=0.25,<0.27
      → language grammars (python/c/javascript/typescript)
  → marko>=2.1.2,<3 (format-markdown extra)
docling-core[chunking-openai]
  → tiktoken>=0.9,<0.13
~~~

这和 WisePen 当前依赖有明显差异：Common 直接依赖 `markdown-it-py` 和 `langchain-text-splitters`，没有 `docling`、`docling-core`、`semchunk`、`transformers`、`tiktoken` 或 `tree-sitter`。增加完整 Docling 会同时引入 parser、normalization、模型初始化和升级耦合。

其他成熟开源实现的可比事实：

- [LlamaIndex MarkdownNodeParser](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/file/markdown.py) 以 heading 行扫描构建 header_path metadata，核心合同是结构 metadata，不是字符 span。
- [LangChain MarkdownHeaderTextSplitter](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/markdown.py) 按 header/line metadata 分组；实验性 splitter 保留 whitespace/code，但不提供稳定 source-span 合同。
- [Unstructured Markdown partition](https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/partition/md.py) 走 Markdown→HTML→element，强调元素类型而非原 Markdown 字符区间。

共同趋势是 parser-aware/structure-aware 的元素 identity 和 metadata；字符级 Markdown provenance 通常需要上游自己维护。

## 8. 能力矩阵

| 能力 | 当前 WisePen | Docling | 建议 |
|---|---|---|---|
| Markdown structure parse | `markdown-it-py` + Node IR | Marko/GFM → DoclingDocument | 参考实现 |
| Section hierarchy | 独立 Section builder，带 span | heading metadata stack | 直接复用本地 |
| Nested list | Node children + list splitter | ListGroup/ListItem | 直接复用本地 |
| Table | Markdown table Node，row/header/span 可控 | TableItem，默认 triplet normalization | 自己实现 |
| Code | language/fence/indent 由本地策略维护 | CodeItem；策略需显式注入 | 自己实现 |
| Formula | Formula Node atomic/fallback | Markdown 常落为 TextItem | 自己实现 |
| Structural packing | Node-specific splitter + generic planner | HierarchicalChunker + Hybrid | 参考实现 |
| Token-aware limit | 当前主要是字符预算 | tokenizer 预算，metadata 也计数 | 封装复用 |
| Oversized fallback | 句子/row/cell/code 规则 | semchunk/line chunker | 封装复用 |
| Header/context metadata | Section、Anchor、source spans | headings/captions/doc_items | 直接复用本地 |
| Provenance | 字符级 `source_spans` + envelope | DocItem identity/ProvenanceItem | 自己实现 |
| Dependency cost | 已有 Markdown 栈 | docling-core + semchunk + transformers/tree-sitter | 不需要完整引入 |

允许的建议词只有“直接复用、封装复用、参考实现、自己实现、不需要”；表中没有把 Docling 当作事实兼容层。

## 9. 方案比较

### A：直接使用 HybridChunker

优点：最少自维护，快速获得结构分组和 token-aware fallback。
代价：默认 serializer 会 normalization；默认 provenance 是 DocItem identity；默认不保留 Markdown 字符 span；code fence、formula、HTML、horizontal rule 语义与 WisePen 不同；依赖和初始化成本较大。
结论：仅适用于不需要原 Markdown 精确回源的独立索引，不适合作为当前 RAG/Chat 主链路。

### B：统一采用 DoclingDocument + HybridChunker

优点：PDF/DOCX/PPTX/Markdown 可以共享 IR。
代价：所有入口要投影到 DoclingDocument；Markdown 已经有 Common Node IR，重复解析；RAG/Chat 当前协议直接收 Markdown；迁移会把 Docling 的 normalization、DocItem identity 和版本耦合传播到所有服务。
结论：当前阶段不采用。只有未来明确要求多格式统一结构 IR，且愿意放弃 Markdown 字符 span 合同，才重新评估。

### C：维护 WisePen Node IR，局部复用算法

保留 `DocumentNode`、Section、Anchor 和 `source_spans`；借鉴 Docling 的阶段边界；可独立引入 semchunk 或 tokenizer adapter；table/code/list/formula 继续由 WisePen 维护。
结论：最符合当前 RAG 引用、contextualization、范围读取和 Chat 缓存合同，作为主推荐。

### D：其他方案

可考虑 LlamaIndex/Unstructured 作为外部 parser，但它们同样主要提供元素/metadata，不解决 WisePen 的字符级 provenance；不足以替代 C。

## 10. Q1–Q6 明确答案

**Q1：HybridChunker 是不是纯 token splitter？**
不是。它先依赖 DoclingDocument 的结构 item 和 serializer，再做 token-aware plain-text fallback，最后按 metadata merge。parser-aware、structure-aware、token-aware 分属不同阶段。

**Q2：它是否保留原 Markdown 字符 span？**
默认不保留。`SerializationResult.spans` 是 DocItem 引用；Markdown backend 本地实验的 provenance 为空。需要字符级 Markdown span 时，WisePen 必须继续自己维护。

**Q3：heading/caption 是否进入预算和 chunk text？**
默认 heading 不进 text，但 contextualize 时进入 token 预算；caption 也计入 metadata。metadata 太大时 Hybrid 可能移除 heading/caption 再递归。

**Q4：table/list/code/formula 是否有 WisePen 同等语义？**
没有。table 默认变成 triplet；list 有结构 identity 但超限 item 会转 plain text；code strategy 不是 Hybrid 默认路径；Markdown `$$` 本地为 TextItem；HTML/hr 结构会丢失。

**Q5：是否有 overlap 和严格 hard/soft limit？**
Hybrid 没有 soft limit，也没有默认 overlap；只有 `max_tokens`。大多数路径按预算切分，但 table metadata overhead 可使 contextualized token 数超过 max。

**Q6：是否值得将 DoclingDocument 作为 WisePen 跨格式统一 IR？**
当前不值得。除非产品明确接受 normalization、DocItem provenance 和完整 Docling 依赖；目前推荐 C，先保持 Markdown-first Node IR。

## 11. 最终建议和后续边界

1. Common 继续把 Node/Section/source span 作为权威事实；不要把 Docling serializer 输出当原文。
2. 如果需要 token-aware chunking，优先封装 tokenizer 和 `semchunk` 的 offset/overlap 能力，并明确 text span 与合成文本 span 的区别。
3. 采用 Docling 的阶段划分作为设计参考：结构解析 → 结构装箱 → token fallback → metadata merge。
4. table 重复 header、code fence、formula atomicity、list item 边界继续保持 WisePen 自有合同；重复内容不生成虚假来源范围。
5. 只有当跨 PDF/DOCX/PPTX 的统一结构 IR 成为产品要求时，才单独做 DoclingDocument 适配层评估。

本报告未修改现有项目代码；临时实验脚本、fixture 和日志均在仓库外。
