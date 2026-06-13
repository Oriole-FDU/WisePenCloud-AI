# Chunking Engine 说明文档

## 概述

通用分块引擎，将文档按 pipeline 配置执行分块流程，输出结构化的 chunk 列表和定位索引。

## 架构

### 流程

```
ChunkDocument → [预处理] → [切分] → [聚合] → [后处理] → [额外语义索引] → ChunkingResult
```

每一步由协议（Protocol）定义接口，具体组件实现协议，通过 `ChunkingPipeline` 组装。

### 核心模型

| 模型 | 说明 |
|------|------|
| `ChunkDocument` | 待分块文档（输入） |
| `TextUnit` | 切分过程中的语义单元（中间产物） |
| `Chunk` | 最终输出分块，含 `level`、`parent_chunk_id` 等字段 |
| `ChunkIndex` | 额外语义索引，提供按维度查找 chunk 的能力 |
| `ChunkingResult` | 分块结果（输出） |

### 枚举

| 枚举 | 值 | 说明 |
|------|------|------|
| `UnitType` | HEADING / PARAGRAPH / TABLE / CODE / FORMULA / IMAGE / LIST / QUOTE / PAGE_MARKER / UNKNOWN | TextUnit 语义块类型 |
| `ChunkLevel` | READ / RETRIEVE / SEARCH / DEFAULT | Chunk 用途层级 |
| `IndexKind` | SECTION / PAGE / ANCHOR | 额外语义索引类型 |

### 协议

| 协议 | 方法 | 说明 |
|------|------|------|
| `PreProcessor` | `process(document) → ChunkDocument` | 预处理，切分前转换文档 |
| `UnitSplitter` | `split(document) → tuple[TextUnit, ...]` | 切分为 TextUnit |
| `ChunkPacker` | `pack(units) → tuple[Chunk, ...]` | 将 TextUnit 聚合为 Chunk |
| `ChunkPostProcessor` | `process(chunks) → tuple[Chunk, ...]` | 后处理，修正或增强 Chunk |
| `ChunkExtraIndexer` | `index(document, units, chunks) → tuple[ChunkIndex, ...]` | 构建额外语义索引 |

## 索引体系设计

### 两层索引

Chunk 的索引分为两层：**连续索引**（Chunk 自带）和**额外语义索引**（ChunkIndex）。

#### 第一层：连续索引（Chunk 自带字段）

每个 Chunk 自身携带顺序和位置信息，构成天然的连续索引：

| 字段 | 类型 | 说明 |
|------|------|------|
| `chunk_index` | `int` | 按 level 分组的顺序编号，从 0 开始 |
| `start_offset` | `int \| None` | 在原文中的起始字符偏移 |
| `end_offset` | `int \| None` | 在原文中的结束字符偏移 |

**用途**：连续阅读、按顺序遍历、按 offset 跳转。不需要额外构建，分块流程自动填充。

```python
# 连续阅读：按 chunk_index 顺序遍历
for chunk in result.chunks:
    display(chunk.text)

# 跳转到指定位置：按 offset 查找
def find_chunk_at_offset(chunks, offset):
    for chunk in chunks:
        if chunk.start_offset <= offset < chunk.end_offset:
            return chunk
```

#### 第二层：额外语义索引（ChunkIndex + IndexKind）

`ChunkExtraIndexer` 专为 Markdown pipeline 构建，基于 unit_type 精确识别，而非正则扫描。

| IndexKind | name 格式 | 数据来源 | 说明 |
|-----------|-----------|----------|------|
| `SECTION` | `section:一级 > 二级` | HEADING unit | 按章节名定位 chunk |
| `PAGE` | `page:3` | PAGE_MARKER unit（`<!-- page N -->`） | 按页码定位 chunk |
| `ANCHOR` | `anchor:Table 1` | TABLE / FORMULA / 含图片的 PARAGRAPH unit | 按锚标定位 chunk |

```python
# 按章节查找
for idx in result.indexes:
    if idx.kind == IndexKind.SECTION and "安装" in idx.name:
        matching_chunks = [c for c in result.chunks if c.chunk_id in idx.chunk_ids]
```

### 设计原则

1. **连续索引不需要额外构建** — `chunk_index` + `start_offset`/`end_offset` 已足够支持顺序遍历和位置跳转
2. **额外语义索引专为 Markdown** — 基于 unit_type 精确识别，非 Markdown pipeline 不配置 `extra_indexer`
3. **非 Markdown pipeline 不需要额外索引** — `RecursiveTextSplitter` 只产出 PARAGRAPH unit，没有结构化 unit，额外索引无意义

### 页码标记格式

统一使用 `<!-- page N -->` 格式，由文档预处理阶段注入，`MarkdownBlockSplitter` 识别为 PAGE_MARKER unit。

## 下游对接协议

分块引擎的组件之间存在隐含的数据契约，下游实现者必须遵守以下约定，否则上游组件无法正确识别和处理。

### 文档预处理阶段（ChunkDocument 注入约定）

| 约定 | 格式 | 消费者 | 说明 |
|------|------|--------|------|
| 页码标记 | `<!-- page N -->` | `MarkdownBlockSplitter` → `PAGE_MARKER` unit → `ChunkExtraIndexer` page 索引 | 必须独占一行，N 为页码数字。由文档预处理阶段（如 PDF 解析）注入到 Markdown 文本中 |

### TextUnit metadata 约定

Splitter 产出的 TextUnit 必须在 metadata 中携带特定字段，后续组件依赖这些字段：

| unit_type | metadata 字段 | 消费者 | 说明 |
|-----------|--------------|--------|------|
| `HEADING` | `title: str` | `MarkdownPreprocessor`（标题路径注入）、`ChunkExtraIndexer`（section 索引） | 标题文本，不含 `#` 前缀 |
| `HEADING` | `section_path: tuple[str, ...]` | `MarkdownPreprocessor`、`ChunkExtraIndexer` | 从根到当前的标题路径，如 `("快速开始", "安装")` |
| `IMAGE` | `alt: str` | `ChunkExtraIndexer`（anchor 索引提取图号） | 图片 alt 文本，如 `"Figure 2: 示意图"`，用于提取图号锚标 |
| `IMAGE` | `src: str` | 下游展示 | 图片 URL |
| `PAGE_MARKER` | `page_number: str` | `ChunkExtraIndexer`（page 索引） | 页码数字字符串 |

### Chunk ID 格式约定

`ChunkFinalizer` 生成的 chunk_id 格式为 `{prefix}:{level}:{index}:{hash}`：

| level | prefix | 示例 |
|-------|--------|------|
| READ | `read` | `read:0:a1b2c3` |
| RETRIEVE | `child-{parent_id}` | `child-read:0:a1b2c3-0` |
| SEARCH | `search` | `search:0:d4e5f6` |
| DEFAULT | `chunk` | `chunk:0:g7h8i9` |

嵌套分块的子 chunk 通过 `parent_chunk_id` 关联父 chunk，下游可通过此字段实现"命中子 chunk → 取回父 chunk"的上下文回溯。

### ChunkingResult 消费约定

| 场景 | 使用方式 | 依赖字段 |
|------|----------|----------|
| 连续阅读 | 按 `chunk_index` 顺序遍历 | `Chunk.chunk_index`、`Chunk.start_offset`、`Chunk.end_offset` |
| 检索回溯 | 命中子 chunk → 通过 `parent_chunk_id` 取回父 chunk | `Chunk.parent_chunk_id`、`Chunk.level` |
| 按章节查找 | 遍历 `indexes`，按 `kind=SECTION` 过滤 | `ChunkIndex.kind`、`ChunkIndex.name`、`ChunkIndex.chunk_ids` |
| 按页码查找 | 遍历 `indexes`，按 `kind=PAGE` 过滤 | `ChunkIndex.kind`、`ChunkIndex.name`、`ChunkIndex.chunk_ids` |
| 按锚标查找 | 遍历 `indexes`，按 `kind=ANCHOR` 过滤 | `ChunkIndex.kind`、`ChunkIndex.name`、`ChunkIndex.chunk_ids` |

## 组件

### 预处理器（pre_processors/）

| 组件 | 说明 |
|------|------|
| `MarkdownPreProcessor` | 为 Markdown 标题下的正文注入 `Section: 一级 > 二级` 路径前缀 |

### 切分器（splitters/）

| 组件 | 说明 |
|------|------|
| `MarkdownBlockSplitter` | 按 Markdown 结构切分（标题/段落/代码块/表格/图片/页码标记等），产出 TextUnit |
| `RecursiveTextSplitter` | 基于 langchain RecursiveCharacterTextSplitter，按递归分隔符切分，适用于无结构文本 |

### 聚合器（packers/）

| 组件 | 说明 |
|------|------|
| `BlockAwarePacker` | 将相邻 TextUnit 聚合成目标大小的 Chunk，不从 unit 中间切开 |

当 pipeline 不配置 packer 时，engine 自动将每个 TextUnit 一对一映射为 Chunk。

### 后处理器（post_processors/）

| 组件 | 说明 |
|------|------|
| `ChunkFinalizer` | 三步修正：①纯标题合并 ②短尾合并 ③ID 生成（`{prefix}:{level}:{index}:{hash}`） |
| `NestedChunkProcessor` | 嵌套分块：将父 chunk 拆分为子 chunk，子 chunk 通过 `parent_chunk_id` 关联父 chunk |

### 额外语义索引器（extra_indexers/）

| 组件 | 说明 |
|------|------|
| `ChunkExtraIndexer` | 专为 Markdown pipeline 构建额外语义索引（section / page / anchor），基于 unit_type 精确识别 |

## 预设 Pipeline

| Pipeline | 适用场景 | 流程 |
|----------|----------|------|
| `MARKDOWN_PIPELINE` | Markdown 文档 | 标题路径注入 → 结构块切分 → 块感知聚合 → 终态处理 → 额外索引 |
| `PLAIN_TEXT_PIPELINE` | 纯文本/无结构文档 | 递归字符切分 → 终态处理 |
| `MARKDOWN_RECURSIVE_PIPELINE` | Markdown 文档（不需要保留完整结构块） | 标题路径注入 → 递归字符切分 → 终态处理 → 额外索引 |
| `SEQUENTIAL_READ_PIPELINE` | 连续读取（电子书翻页等） | 递归字符切分（无重叠） → 终态处理 |
| `NESTED_MARKDOWN_PIPELINE` | 精准检索 + 完整上下文的 RAG | 标题路径注入 → 结构块切分 → 块感知聚合 → 终态处理 → 嵌套拆分 → 额外索引 |

### 配置常量

- `DEFAULT_CHUNK_SIZE = 4000`：默认 chunk 目标字符数，所有预设 pipeline 统一引用

### 嵌套分块原理

```
父 chunk (level=READ, ~4000字)
├── 子 chunk 0 (level=RETRIEVE, ~600字, parent_chunk_id=父chunk_id)
├── 子 chunk 1 (level=RETRIEVE, ~600字, parent_chunk_id=父chunk_id)
└── 子 chunk 2 (level=RETRIEVE, ~600字, parent_chunk_id=父chunk_id)
```

检索时命中子 chunk → 通过 `parent_chunk_id` 取回父 chunk 作为完整上下文注入 LLM。

## 用法

```python
from chat.application.tools.common.chunking_engine.core.engine import ChunkingEngine
from chat.application.tools.common.chunking_engine.core.models import ChunkDocument, IndexKind
from chat.application.tools.common.chunking_engine.presets import MARKDOWN_PIPELINE

engine = ChunkingEngine()
result = engine.chunk(
    document=ChunkDocument(text="# 标题\n正文内容...", content_type="text/markdown"),
    pipeline=MARKDOWN_PIPELINE,
)

# 连续阅读：按 chunk_index 顺序遍历
for chunk in result.chunks:
    print(f"[{chunk.level}] {chunk.chunk_id}: {chunk.text[:80]}...")

# 按章节查找：利用额外语义索引
for idx in result.indexes:
    if idx.kind == IndexKind.SECTION:
        print(f"章节 {idx.name} 包含 chunk: {idx.chunk_ids}")
```

## 目录结构

```
chunking_engine/
├── __init__.py
├── presets.py              # 预设 pipeline 和配置常量
├── core/
│   ├── models.py           # 数据模型（ChunkDocument / TextUnit / Chunk / ChunkIndex / ChunkingResult）
│   ├── protocols.py        # 协议定义（PreProcessor / UnitSplitter / ChunkPacker / ChunkPostProcessor / ChunkExtraIndexer）
│   ├── pipeline.py         # ChunkingPipeline 配置
│   └── engine.py           # ChunkingEngine 引擎
├── pre_processors/
│   └── markdown_preprocessor.py  # Markdown 标题路径注入
├── splitters/
│   ├── markdown_block_splitter.py  # Markdown 结构块切分（含页码标记识别）
│   └── recursive_text_splitter.py  # 递归字符切分
├── packers/
│   └── block_aware_packer.py       # 块感知聚合
├── post_processors/
│   ├── chunk_finalizer.py          # 终态处理（合并 + ID 生成）
│   └── nested_chunk_processor.py   # 嵌套分块
└── extra_indexers/
    └── chunk_extral_indexer.py     # 额外语义索引器（专为 Markdown）
```
