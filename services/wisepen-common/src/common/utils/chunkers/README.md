# Chunkers

这个目录提供面向工具内容的通用文本分块。它负责把原始正文转换为可持久化的扁平 `Chunk`，并保留从 chunk 回到原文和 Markdown
结构的定位信息。

当前生产调用方是 `ToolContentStore`。RAG 的父子分块、索引文本增强和命中后上下文展开属于 RAG 应用层，后续直接放在
`application/rag`，不放进这里的通用 API。

## 公开入口

### Markdown

```python
from common.utils.chunkers import ChunkDocument, MarkdownChunker

result = MarkdownChunker().chunk(
    document=ChunkDocument(
        text=markdown,
        content_type="text/markdown",
    )
)
```

`result` 包含：

- `chunks`：Tool Content 实际保存和读取的扁平分块；
- `blocks`：Markdown parser 识别出的原始结构块；
- `locators`：Section、Page、Anchor 到最终 chunk 的映射；
- `metadata["strategy"]`：本次实际采用的 Markdown 策略。

### 普通文本

```python
from common.utils.chunkers import ChunkDocument, PlainTextChunker

result = PlainTextChunker().chunk(document=ChunkDocument(text=text))
```

普通文本没有 Markdown locator，按段落、换行、句子和空格逐级回退切分，并保存准确 offset。

## 数据流

```text
原始正文
  -> MarkdownParser / plain text splitter
  -> TextBlock
  -> strategy 路由与 block 装箱
  -> Chunk + SourceSpan
  -> Markdown locator
  -> ToolContentStore 投影并持久化
```

`TextBlock` 是解析阶段的结构单元，`Chunk` 是调用方消费的最终单元。两者不能混用：一个 chunk 可以包含多个 block，一个
oversized block 也可能被拆成多个 chunk。

## 原文映射

每个 `Chunk` 都保存 `source_spans`。span 使用左闭右开的原文字符区间：

```python
source_text = "\n\n".join(
    document.text[span.start_offset:span.end_offset].strip()
    for span in chunk.source_spans
)
```

`start_offset/end_offset` 只表示 chunk 覆盖的最外层范围。页码标记等未进入 chunk 正文的内容可能位于这个范围内，因此需要精确回读正文时必须使用
`source_spans`。

`ToolContentStore` 只保存 spans 和结构 metadata，不重复保存 chunk 文本；读取时从存储的权威正文按 spans 物化内容。

## Markdown 解析

`MarkdownParser` 使用 `markdown-it-py` 解析顶层块，并保留每个块在原文中的 offset。

当前识别：

| Markdown 内容          | `BlockKind`   | 额外信息                                      |
|----------------------|---------------|-------------------------------------------|
| 标题                   | `HEADING`     | `heading_level`、`title`、完整 `section_path` |
| 普通段落                 | `PARAGRAPH`   | 当前 `section_path`                         |
| pipe/HTML 表格         | `TABLE`       | 上置或下置编号表题合并后可带 `anchor_label`          |
| fenced/indented code | `CODE`        | 当前 `section_path`                         |
| 列表、引用、公式             | 对应类型          | 当前 `section_path`                         |
| 独占图片段落               | `FIGURE`      | 上置或下置编号图题合并后可带 `anchor_label`          |
| 含图片的普通段落            | `PARAGRAPH`   | 图片语法保留在段落原文中                            |
| `<!-- page N -->`    | `PAGE_MARKER` | `page_label`                              |

`TextBlock.text` 保留对应 offset 范围内的原始文本，不裁剪首尾空白或换行。

标题栈按真实层级维护。例如：

```markdown
# 产品

## 安装

### Windows
```

`Windows` 标题及其正文的 `section_path` 是 `("产品", "安装", "Windows")`。

普通 Markdown 中独立的表题/图题段落会在满足以下条件时与相邻表格/图片合并：标签可识别、两者只隔空白、且中间没有 page
marker。表题和图题都支持位于主体之前或之后；这样编号和主体不会被拆开，并可生成 `anchor:Table N` 或 `anchor:Figure N`。

caption 不作为独立 `BlockKind` 或 metadata 保存。parser 只保留合并后的原文范围和实际供 locator 使用的 `anchor_label`。

## 页码契约

当前 parser 识别的页码标记是：

```text
<!-- page 1 -->
```

页码标记本身不进入 chunk 正文。parser 将该页码投影给后续 blocks，直到遇到下一个 page marker。

## Markdown 策略

`MarkdownChunkerConfig.strategy` 只接受 `MarkdownChunkingStrategy` 枚举。

| 配置         | 有 page marker     | 无 page marker   |
|------------|-------------------|-----------------|
| `AUTO`     | 实际使用 `BY_PAGE`    | 实际使用 `BY_TITLE` |
| `BY_PAGE`  | 按页处理              | 抛出 `ValueError` |
| `BY_TITLE` | page 只作为 metadata | 按标题结构处理         |

实际策略写入 `ChunkingResult.metadata["strategy"]`，调用方不需要重复猜测。

### `BY_PAGE`

每个 page marker 开启一页，下一 marker 结束上一页：

```text
page blocks <= max_characters
  -> 整页一个 chunk

page blocks > max_characters
  -> 只在当前页内按完整 block 装箱
  -> 单个 block 仍超长时使用通用 Markdown 递归回退
```

`new_after_n_chars` 不影响正常页整页保留。页只在超过 `max_characters` 时拆分，拆出的 chunk 不会跨页。

### `BY_TITLE`

标题控制语义 pre-chunk：

- 连续标题与它们后面的第一段正文保持在一起；
- 已经出现正文后再遇到标题，开始下一个 pre-chunk；
- page marker 不切断 Section，因此一个 Section 可以跨页；
- 每个 pre-chunk 再按完整 block 装箱，超过上限才继续拆分。

这个行为保证标题路径参与边界决策，同时避免把“一个 chunk 必须等于一个 Section”写死。短 Section 可以保持紧凑，长 Section
也能在上下文长度受控的前提下拆开。

## 长度与回退

Markdown 采用两个字符限制：

- `new_after_n_chars`：`BY_TITLE` 下达到该长度后，在下一个完整 block 前开始新 chunk；
- `max_characters`：单个 chunk 的硬上限。

字符限制是当前通用 Tool Content 场景的明确契约。只有单个 block 超过硬上限时，才使用同一套 Markdown separator 递归回退。

正常装箱没有 overlap，避免重复证据和 offset 歧义。普通文本可通过 `PlainTextChunkerConfig.chunk_overlap` 显式配置 overlap。

## Locator

Markdown 分块完成后构造三类 locator：

- `SECTION`：完整标题路径对应的原文范围和 chunk 集合；
- `PAGE`：页码对应的原文范围和 chunk 集合；
- `ANCHOR`：表格、公式等可识别标签对应的 chunk 集合。

locator 与最终 chunks 一起生成，所以不会引用分块过程中已经失效的临时 ID。一个 locator 可以指向多个 chunks，一个 chunk
也可以属于多个 Section 或 Page locator。

## Tool Content 集成

`ToolContentStore` 根据 `content_type` 路由：

```text
text/markdown -> MarkdownChunker(AUTO)
其他文本      -> PlainTextChunker
```

Markdown 的默认结果是：带页码正文按页保存，无页码正文按标题保存。Store 把通用模型投影为：

- `source_spans`：读取正文；
- `block_kinds`：按内容结构筛选；
- `section_paths`：按章节定位；
- `page_labels`：按页定位；
- `anchor_labels`：按表格、公式标签定位。

修改 chunk 契约时，必须同步检查 `tool_content_store` 与 `tool_content_read`，因为它们是当前真实调用链。

## 测试重点

相关测试位于 `src/chat/tests/chunkers` 和 `src/chat/tests/tool_content_store`。至少覆盖：

- AUTO 的 page/title 路由；
- 正常页整页保留与超长页页内回退；
- Section 跨页与标题层级；
- chunk 文本可由 `source_spans` 精确重建；
- table caption、page、section、anchor locator；
- Tool Content 的存储、selector 与窗口读取。

新增策略时，应新增显式枚举和独立测试，不把输入特征判断散落到调用方。
