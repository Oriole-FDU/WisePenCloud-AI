# Chunkers

这个目录把原始文本转换为用于语义检索的 `Chunk`，同时保留可直接回源读取的 Markdown locator。chunk 和 locator 是两套独立结构：前者服务召回，后者服务确定性定位，物理页码不会强制切断语义 chunk。

## 公开入口

```python
from chat.application.utils.chunkers import ChunkDocument, MarkdownChunker, PlainTextChunker

markdown_result = MarkdownChunker(max_characters=6000).chunk(
    document=ChunkDocument(text=markdown, content_type="text/markdown")
)
plain_result = PlainTextChunker().chunk(document=ChunkDocument(text=text))
```

`ChunkingResult` 包含：

- `chunks`：用于排序和召回的语义分片；
- `blocks`：parser 识别出的 Markdown 结构块；
- `locators`：内部原文索引，用于构建 page、section、anchor 三类结构入口。

普通文本没有 Markdown locator，按段落、换行、句子和空格逐级回退切分。

## 数据流

```text
原始正文
  -> MarkdownParser / plain text splitter
  -> TextBlock
  -> 语义 Section 装箱
  -> Chunk + SourceSpan（检索）

Markdown TextBlock
  -> TextLocator（直接回源）
```

`TextBlock` 是解析阶段的结构单元。`Chunk` 可以包含多个 block；只有语义单元超过 `max_characters` 时才继续按完整 block 装箱，单个超长 block 才使用递归 splitter 兜底。

## 原文映射

每个 chunk 的 `source_spans` 是权威证据范围，使用左闭右开的原文字符区间。chunk 文本按以下方式物化：

```python
source_text = "\n\n".join(
    document.text[span.start_offset:span.end_offset].strip()
    for span in chunk.source_spans
)
```

`start_offset/end_offset` 只是 chunk 覆盖的最外层范围；精确回读必须使用 `source_spans`。`ToolContentStore` 不重复持久化 chunk 文本，读取时从权威原文按 spans 物化。

## Markdown 解析

`MarkdownParser` 使用 `markdown-it-py` 解析顶层块并保留原文 offset。当前识别标题、段落、表格、代码、列表、引用、公式、独占图片和 `<!-- page N -->` page marker。

标题栈维护完整 `section_path`。独立表题或图题在标签可识别、只与主体间隔空白且中间没有 page marker 时，会与相邻表格或独占图片合并，支持 caption 位于主体上方或下方。caption 不保存为独立类型，只为合并后的主体提供 `anchor_label`。

page marker 不进入 chunk 正文，但 parser 会把 `page_label` 投影给后续 blocks，直到下一个 marker。marker 自身和页范围仍保留在 Page locator 中。

## 语义分块

Markdown 只按标题语义组织 chunk：

- 连续标题与其后第一段正文保持在一起；
- 已出现正文后遇到新标题，开始下一个语义单元；
- page marker 不触发 flush，因此一个 Section 和一个 chunk 都可以跨页；
- 语义单元超过 `max_characters` 后才按完整 block 继续装箱；
- 单个 block 超过硬上限时，使用 Markdown 递归 splitter 兜底。

字符上限是检索上下文的安全边界，不代表文档结构。正常装箱没有 overlap，避免重复证据和 offset 歧义。

## Locator

Markdown parser 结果直接生成三类 `TextLocator`：

- `SECTION`：从标题开始，到下一个同级或更高层级标题之前；
- `PAGE`：从 page marker 开始，到下一个 page marker 之前；
- `ANCHOR`：带有 `anchor_label` 的表格或独占图片原文范围。

locator 只保存 `name`、`kind`、`start_offset` 和 `end_offset`，不引用 chunk。读取方按 locator 的 offset 直接从完整原文截取内容；同名 locator 可以返回多个原文窗口。

## Tool Content 集成

`ToolContentStore` 根据 `content_type` 路由：

```text
text/markdown -> MarkdownChunker
其他文本      -> PlainTextChunker
```

消费契约是：

- `tool_content_get_snapshot` 返回 pages、section tree 和 anchors，不读取正文；
- `tool_content_semantic_search` 对语义 chunks 排序，再按 `source_spans` 回源；
- `tool_content_regex_search` 在完整原文上匹配，不受 chunk 边界影响；
- `tool_content_read_range` 按字符 offset 直接读取原文；
- `tool_content_read_pages` 按 page label 批量读取原文；
- `tool_content_read_sections` 按 section path 批量读取原文。

其中按结构读取（`tool_content_read_pages` / `tool_content_read_sections`）是独立能力，专门覆盖“见某某章节”“见某某页”这类稳定定位需求，不依赖图抽取，也不要求先做语义召回。

不存在 selector、按页预过滤或相邻 chunk 展开。页码只是一种可定位的文档信息，不是分块策略。

## 测试重点

相关测试位于 Chat Service 的 `src/chat/tests/chunkers`、`tool_content_store` 和 `session_tools`，并覆盖：

- Section 跨页且 page marker 不切断 chunk；
- 超长语义单元的硬上限回退；
- chunk 文本可由 `source_spans` 精确重建；
- page、section、anchor locator 的原文 offset；
- locator 直接读取、跨 chunk 正则匹配和语义检索回源。
