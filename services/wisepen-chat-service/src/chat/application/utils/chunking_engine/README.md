# Chunking Engine

`ChunkingEngine` 把一段文本切成可读取、可检索、可定位的 chunks。它是 ToolContentStore 的下游基础设施之一，但不关心 Redis、不关心 content_id，也不关心模型输出格式。

这份文档只说明接手者最需要知道的事：怎么选 pipeline、输出里哪些字段可靠、不要依赖哪些内部细节。

## 什么时候用

适合：

- 工具大输出入库前切块。
- Markdown / 文档解析结果生成章节、页码、锚点索引。
- `tool_content_read` 后续按 chunk、section、anchor 读取窗口。

不适合：

- 网页抓取。
- 文档解析。
- 排序和 rerank。
- 模型可见文本渲染。

## 基本流程

```text
ChunkDocument
  -> PreProcessor
  -> UnitSplitter
  -> ChunkPacker
  -> ChunkPostProcessor
  -> ChunkExtraIndexer
  -> ChunkingResult
```

工具或 Store 一般不需要关心每一步，只需要选择合适的 preset 名称。

## 推荐 Pipeline

| Pipeline | 什么时候用 | 说明 |
| --- | --- | --- |
| `markdown` | Markdown、网页正文、document_parse Markdown | 默认首选，保留标题/表格/代码块等结构 |
| `plain_text` | 无结构纯文本 | 没有 section/page/anchor 索引 |
| `markdown_recursive` | Markdown 很长且结构块过大 | 会更偏字符递归切分 |
| `nested_markdown` | 后续需要父子块召回 | 当前不要默认使用，除非明确需要嵌套语义 |

ToolContentStore 默认规则：

- `content_type == "text/markdown"` 使用 `markdown`
- 其它文本使用 `plain_text`

## 下游应该依赖的字段

对于 `tool_content_read` 和 Store，目前稳定依赖这些字段：

### Chunk

- `chunk_index`: 当前 content 内的顺序号。
- `start_offset`: chunk 在原文中的起始字符偏移。
- `end_offset`: chunk 在原文中的结束字符偏移。
- `metadata["unit_types"]`: 该 chunk 覆盖的结构类型。
- `metadata["section_paths"]`: 章节路径。
- `metadata["anchor_names"]`: 表格、图片、公式等锚点名。

ToolContentStore 会把这些字段收敛成更小的 `ToolContentChunk`：

- `chunk_index`
- `start_offset / end_offset`
- `unit_types`
- `section_path`
- `anchor_names`

### ChunkIndex

`ChunkExtraIndexer` 会产生额外索引：

| kind | name 示例 | 用途 |
| --- | --- | --- |
| `SECTION` | `section:快速开始 > 安装` | 按章节筛选 |
| `PAGE` | `page:3` | 按页码筛选 |
| `ANCHOR` | `anchor:Table 1` | 按表格/图片/公式锚点筛选 |

ToolContentRead 现在主要按 `entry.name` 和 `entry.chunk_indices` 使用索引，不依赖 `chunk_ids`。

## 不要依赖的内部细节

这些字段在 chunking engine 内部有意义，但不要让上层工具或 Store 过度依赖：

- `chunk_id`
- `parent_chunk_id`
- `level`
- `content_hash`
- `start_unit / end_unit`

原因：上层读取窗口真正需要的是 content 内顺序、offset 和结构索引。把内部 ID/层级暴露到 ToolContentStore 会让模型和调用方误以为可以跨内容长期引用，实际并不稳定。

## Markdown 结构识别约定

Markdown pipeline 会识别：

- 标题
- 段落
- 列表
- 代码块
- 表格
- 图片
- 引用
- 页码标记

页码标记格式：

```markdown
<!-- page 3 -->
```

这个标记应由 document_parse 或预处理阶段注入，并独占一行。

## 最小用法

```python
from chat.application.utils.chunking_engine import ChunkDocument, ChunkingEngine
from chat.application.utils.chunking_engine.registry import get_chunking_pipeline

engine = ChunkingEngine()
result = engine.chunk(
    document=ChunkDocument(
        text="# 标题\n\n正文内容",
        content_type="text/markdown",
    ),
    pipeline=get_chunking_pipeline("markdown"),
)

for chunk in result.chunks:
    print(chunk.chunk_index, chunk.start_offset, chunk.end_offset)
```

## Review 时重点看什么

- 业务是否选了合适的 pipeline。
- Markdown 文本是否保留标题、表格、页码标记等结构。
- 是否在上层依赖了 `chunk_id/parent_chunk_id/level` 这类内部字段。
- 是否把 chunking engine 当成 ranking 或解析器使用。
- 是否把很长文本先切块再让模型读取，而不是一次性塞进上下文。

## 目录提示

```text
chunking_engine/
├── models.py           # 数据模型
├── protocols.py        # 协议接口
├── engine.py           # 分块引擎
├── pipeline.py         # 管线配置
├── pre_processors/     # Markdown 标题路径注入
├── splitters/          # Markdown 块切分、递归文本切分
├── packers/            # TextUnit 聚合为 Chunk
├── post_processors/    # Chunk 终态处理
├── extra_indexers/     # section/page/anchor 索引
└── presets.py          # 推荐 pipeline
```
