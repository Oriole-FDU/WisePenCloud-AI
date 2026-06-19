# Utils 能力盘点

本文只记录当前仓库已经沉淀的共享实现入口，方便快速定位“现成能力在哪里”。

规则、职责边界、开发流程判断放到：

- `docs/team/03-shared-engines-and-dev-flow.md`

## 目录边界

| 路径 | 定位 | 使用边界 |
| --- | --- | --- |
| `src/chat/application/utils/` | 应用层通用基础能力 | 不直接绑定某个 tool，可被多个应用服务或 tool 复用 |
| `src/chat/application/tools/utils/` | 工具层辅助能力 | 面向 tool 输入输出、文件识别、Markdown 渲染等工具侧需求 |

这里不重复写团队规则，只说明当前已有能力入口。

## Chunking Engine

路径：`src/chat/application/utils/chunking_engine/`

用途：把文本切成可读取、可检索、可定位的 chunks。

稳定入口：

- `ChunkingEngine`
- `ChunkDocument`
- `ChunkingPipeline`
- `ChunkingResult`
- `Chunk`
- `ChunkIndex`
- `UnitType`
- `IndexKind`

推荐 pipeline 在 `presets.py`：

| Pipeline | 适用场景 |
| --- | --- |
| `MARKDOWN_PIPELINE` | Markdown、网页正文、`document_parse` Markdown；默认首选 |
| `PLAIN_TEXT_PIPELINE` | 无结构纯文本 |
| `MARKDOWN_RECURSIVE_PIPELINE` | Markdown 很长且结构块过大，需要更均匀切分 |
| `NESTED_MARKDOWN_PIPELINE` | 需要父子块召回的 RAG 场景，当前不要默认使用 |

下游稳定入口：

- `chunk_index`
- `start_offset` / `end_offset`
- `metadata["unit_types"]`
- `metadata["section_paths"]`
- `metadata["anchor_names"]`
- `ChunkIndex.name`
- `ChunkIndex.kind`
- `ChunkIndex.chunk_indices`

## Ranking Engine

路径：`src/chat/application/utils/ranking_engine/`

用途：对业务候选做可解释排序。

稳定入口：

- `RankingEngine`
- `RankingPipeline`
- `RankRequest`
- `RankQuery`
- `RankCandidate`
- `RankResult`
- `RankedCandidate`
- `ScoreSignal`
- `TOOL_CONTENT_READ_RANKED_EXPAND_ENGINE`

现有组件：

| 子目录 | 能力 |
| --- | --- |
| `scorers/` | `BM25Scorer`、`FieldedBM25Scorer`、`PriorRankScorer`、`KeywordScorer`、`DenseVectorScorer` |
| `fusion/` | `WeightedRrfFusion`，当前默认推荐融合方式 |
| `rerankers/` | `ZeroEntropyReranker` |
| `diversifiers/` | `MmrDiversifier`、`GroupRoundRobinDiversifier`、`MaxMinDiversifier` |
| `text/` | `RankingTokenizer`、领域词典加载、停用词 |

接入时先把业务对象转成 `RankCandidate`，排序后再映射回业务对象。字段名应表达真实语义，例如 `section`、`anchor`、`title`、`snippet`，不要用 metadata 魔法 key 替代明确字段。

## LLM Clients

路径：`src/chat/application/utils/llm_clients/`

用途：提供轻量 LiteLLM query / embedding client，适合小范围内部任务复用。主聊天链路仍通过 `chat.core.providers.LiteLLMAdapter`。

现有入口：

- `LiteLLMQueryClient`
- `QueryResult`
- `query_client`
- `LiteLLMEmbeddingClient`
- `EmbeddingResult`
- `embedding_client`

## 文件类型识别

路径：`src/chat/application/tools/utils/file_type_detect.py`

用途：基于 Magika 识别本地文件类型，失败时回退到扩展名和 `mimetypes.guess_type`。

现有入口：

- `detect_file_type(file_path) -> FileType`
- `detect_mime_type(file_path) -> str`
- `FileType(label, mime_type)`

## Markdown 渲染器

路径：`src/chat/application/tools/utils/markdown_renderer/`

用途：把结构化表格或 HTML 渲染成适合模型阅读、缓存和分块的 Markdown。

现有入口：

- `TableMarkdownRenderer`
- `TableMarkdownRenderResult`
- `TableRenderInfo`
- `FragmentMarkdownRenderer`
- `WebPageMarkdownRenderer`

### TableMarkdownRenderer

将二维表格数据渲染为 GitHub-flavored Markdown 表格，负责：

- 输入形态归一化。
- 单元格换行、反斜杠、竖线转义。
- 行列截断。
- 返回渲染规模和截断元信息。

支持三种调用形态：

- `render([["姓名", "分数"], ["Alice", 98]])`
- `render(headers=["姓名", "分数"], rows=[["Alice", 98]])`
- `render(data=[["Alice", 98]], headers=["姓名", "分数"])`

适用场景：Excel、OCR 表格、外部 API 返回的表格矩阵。

### FragmentMarkdownRenderer

将 HTML 片段直接转 Markdown，并可移除 `script`、`style`、`noscript`、`template`、`svg`、`canvas` 等噪声标签。

适用场景：OCR 或外部服务返回的局部 HTML 表格/片段。

### WebPageMarkdownRenderer

基于 `trafilatura.extract` 提取网页主体并输出 Markdown。

适用场景：后续 web fetch / web reader 工具。它只负责 HTML 到正文 Markdown，不负责下载网页、URL 安全校验或工具返回缓存。

## 快速定位

新增业务能力前先检查：

- 需要把大文本切块、生成章节/页码/锚点索引：用 `chunking_engine`。
- 需要对候选排序、融合、多样性控制：用 `ranking_engine`。
- 需要轻量调用模型或 embedding：用 `llm_clients`。
- 需要识别本地文件类型：用 `detect_file_type` / `detect_mime_type`。
- 需要表格转 Markdown：用 `TableMarkdownRenderer`。
- 需要 HTML 片段转 Markdown：用 `FragmentMarkdownRenderer`。
- 需要网页主体抽取成 Markdown：用 `WebPageMarkdownRenderer`。

## 文档入口

更细的组件说明见：

- `src/chat/application/utils/chunking_engine/README.md`
- `src/chat/application/utils/ranking_engine/README.md`
- `src/chat/application/utils/ranking_engine/ASSEMBLY_GUIDE.md`
- `docs/team/03-shared-engines-and-dev-flow.md`
