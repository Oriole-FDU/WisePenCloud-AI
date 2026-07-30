# RAG 内容投影实体设计

这份文档解释 RAG 内容投影中的实体为什么存在、分别解决什么问题，以及它们如何组成一次完整的更新和读取链路。

这里的设计目标不是把 Mongo 设计成一份最小化的规范化数据表，而是让 RAG 能够稳定完成：

```text
Kafka 正文
  -> 生成可检索的内容投影
  -> 在多个后端之间发布同一版本
  -> 从命中的 chunk 回到原文证据
  -> 沿 Section 读取完整上下文
  -> 为图抽取提供稳定正文
```

因此，部分实体之间存在字段重复是有意的。重复字段换取的是读取边界清晰、版本过滤简单，以及不必在每次查询时重新拼装整套文档结构。

## 先看整体关系

一次 Kafka `DocumentReady` 事件进入 RAG 后，会生成一套内容投影：

```text
RagDocumentContent
        |
        v
RagContentRevisionDocument
        |
        +-- RagContentPartDocument[]
        +-- RagSectionDocument[]
        +-- RagSectionReadingBlockDocument[]
        +-- RagSourceRefDocument[]
        |
        +-- Qdrant retrieval chunks
        +-- Neo4j graph projection

RagProjectionCheckpointDocument
        ^
        |
        指向当前 staged / applied 的 content revision
```

最重要的关系是：

```text
一个 resource
  -> 一个当前 checkpoint
  -> 一个当前 applied content_revision
  -> 该 revision 下的一整套 parts / sections / reading blocks / source refs
```

`content_revision` 是这套数据的共同版本边界。查询任何内容实体时，都必须先从 checkpoint 得到当前 `applied_content_revision`
，再按这个 revision 读取，不能跨 revision 混用。

## 为什么不能只保存一张 Chunk 表

如果只保存 `chunk_id + text + section_path`，初始向量召回可以工作，但后续会出现几个问题：

- 命中 chunk 后无法稳定恢复一个 Section 的完整上下文；
- 不能区分“正文已经写入”和“向量索引已经发布”的中间状态；
- SourceRef 只能依赖 chunk 中携带的正文副本，难以精确回到 Kafka 原文 offset；
- 图抽取窗口无法从当前 applied 版本恢复整篇 Markdown；
- 文档更新时，旧版本和新版本的数据容易在 Mongo、Qdrant、Neo4j 之间混用。

这些实体不是为了把模型字段拆得更细，而是分别固定了内容投影中的几个边界：

```text
版本边界       ContentRevision
发布边界       ProjectionCheckpoint
正文存储边界   ContentPart
结构边界       Section
阅读边界       ReadingBlock
证据边界       SourceRef
```

## 1. ContentRevision：一套完整投影的身份

### 它解决的问题

上游 `document_version` 只能说明正文来自哪个上游版本，不能完整说明 RAG 产物是什么。

同一个上游正文可能因为以下原因重新生成不同投影：

- Section 投影规则变化；
- chunk 策略变化；
- contextual indexing 规则变化；
- RAG projection schema 变化。

因此 RAG 需要一个独立身份，精确表示：

> 这套 Section、ReadingBlock、SourceRef、向量和图关系，是由哪份正文、哪套投影规则生成的。

### 它承担的职责

`RagContentRevisionDocument` 是一套内容投影的元数据记录，保存：

- `content_revision`：RAG 投影身份；
- `resource_id`：所属资源；
- `document_version`：上游正文版本；
- `content_hash`：整篇正文完整性校验值。

它本身不保存全文，全文由同一 revision 下的 ContentPart 保存。

### 它和其他实体的关系

同一套投影中的所有内容实体都带有相同的 `content_revision`：

```text
rrev_abc
  -> parts
  -> sections
  -> reading_blocks
  -> source_refs
```

所以 ContentRevision 是查询和清理时的共同过滤边界。

## 2. ProjectionCheckpoint：当前发布状态

### 它解决的问题

一次内容更新会同时影响 Mongo、Qdrant 和 Neo4j。它们不能通过一次 Mongo 事务一起提交，因此不能简单地把“Mongo 已写入”当成“RAG
新版本已经可读”。

例如：

```text
Mongo 新版本已写入
Qdrant 向量还没有完成
```

这时如果查询直接读取新版本，就可能得到没有向量、没有 ACL 或没有完整证据的半成品。

### 它承担的职责

`RagProjectionCheckpointDocument` 是每个资源唯一的一条可变状态记录，用来保存两个指针：

```text
staged_content_revision   正在构建或等待发布的 revision
applied_content_revision  当前允许查询的完整 revision
```

它不是历史表，也不保存正文。每次更新通常只是修改这一条记录。

### 一次发布过程

```text
旧状态：
  applied = rrev_old
  staged  = null

开始生成新版本：
  applied = rrev_old
  staged  = rrev_new

Mongo / Qdrant / ACL 写入全部成功：
  applied = rrev_new
  staged  = null
```

如果中途失败：

```text
applied = rrev_old
staged  = rrev_new
```

查询继续读取旧版本，Kafka 重试可以继续处理新版本。这样解决的是跨数据库发布过程的可见性和幂等问题。

`document_version` 负责比较上游事件的新旧顺序，`content_revision` 负责精确识别 RAG 投影产物，两者不是重复状态。

## 3. ContentPart：本地正文回源副本

### 它解决的问题

RAG 查询、evidence materializer 和图抽取都需要根据 source span 回到正文。如果每次都向上游 resource 服务重新取全文，会带来：

- 额外网络延迟；
- 上游正文变化导致 offset 不一致；
- 查询和图抽取无法严格绑定到同一个 applied revision；
- 大文档无法直接作为一个 Mongo 文档保存。

### 它保存什么

ContentPart 保存的是 Kafka `DocumentReady` 消息中的完整 `content`，当前实现假定它已经是上游提供的 Markdown 正文：

```text
Kafka content
  -> RagDocumentContent.markdown
  -> RagContentProjection.markdown
  -> RagContentPartDocument[]
```

Python 侧不会把它改写成另一份语义正文，而是按全局字符 offset 切成若干连续大分片：

```text
part 0: [0, 1_000_000)
part 1: [1_000_000, 2_000_000)
part 2: [2_000_000, end)
```

### 它承担的职责

ContentPart 是 RAG 本地的原文基础：

- SourceRef 根据 spans 读取精确证据；
- 图抽取根据当前 applied revision 恢复全文和 chunk 文本；
- 读取时检查分片连续性；
- 将重新拼接的全文与 ContentRevision 的 `content_hash` 比较。

它不是检索 chunk，也不是 Section。它只是把原文保存成可按 offset 回读的形式。

## 4. Section：文档结构和导航边界

### 它解决的问题

传统 chunk 只能告诉我们“命中了这一段文字”，但 Agent 还需要知道：

- 这段文字属于哪个章节；
- 上一级和下一级标题是什么；
- 前后有哪些同级章节；
- 当前章节自身内容和子章节范围在哪里。

### 它承担的职责

`RagSectionDocument` 是标题树节点，负责保存：

- Section 的稳定 ID；
- parent/children 关系；
- ordinal 顺序；
- 从根到当前节点的 `section_path`；
- 当前 Section 的 `own_start / own_end`；
- 包含子章节的 `subtree_end`；
- 用于导航的 summary。

Section 是 RAG 中的逻辑父节点，但不是传统意义上的“大父 chunk”。它规定结构边界，真正的正文仍然由 ReadingBlock 承载。

## 5. ReadingBlock：Section 内的阅读单元

### 它解决的问题

Section 是稳定的语义边界，但有些 Section 仍然很长，不能每次都把整章一次性放进模型上下文。

如果直接对整篇文档按长度切分，chunk 可能跨越章节，造成上下文割裂；如果完全不切分，长章节又会超出上下文预算。

### 它承担的职责

`RagSectionReadingBlockDocument` 在 Section 内生成有界、按顺序排列的正文块：

```text
一个 Section
  -> 一个或多个 ReadingBlock
```

关键约束是：

```text
ReadingBlock 不跨 Section
```

它保存原文 `raw_text`，以及对应的 `source_spans`、页码和锚点标签，供 Section 导航直接返回。

ReadingBlock 是“模型阅读上下文”的主要单位；RetrievalChunk 则是“检索召回”的主要单位。

## 6. SourceRef：从检索结果回到原文的证据索引

### 它解决的问题

Qdrant 返回的是检索 candidate，不是最终可信正文。模型回答、evidence materializer 和图抽取都需要知道：

> 这个 chunk 到底对应 Kafka 正文中的哪一段？属于哪个 Section？覆盖哪些页码和锚点？

### 它承担的职责

`RagSourceRefDocument` 是一条 chunk 到原文的稳定定位记录：

```text
chunk_id
  -> section_id
  -> source_spans
  -> ContentPart
  -> 原文正文
```

它保存的是定位信息和来源元数据，不重复保存完整正文。查询时先由 checkpoint 选定 applied revision，再通过 SourceRef 的 spans
从对应 ContentPart 中读取内容。

当前实现中，一个 retrieval chunk 对应一个 SourceRef，但一个 Section 可以拥有多个 SourceRef。

## 一次查询如何使用这些实体

以 Agent 命中一个检索 chunk 为例：

```text
1. Qdrant 返回 chunk_id、section_id、source_ref_id 和 content_revision。
2. RAG 查询根据 resource_id 读取 checkpoint。
3. checkpoint.applied_content_revision 选出当前可读版本。
4. 按 applied revision 查询 SourceRef。
5. SourceRef 给出 source_spans 和 section_id。
6. 按 source spans 读取对应 ContentPart。
7. 返回命中的证据和所属 ReadingBlock。
8. 如果 Agent 要继续阅读，则按 section_id 读取 Section 和全部 ReadingBlock。
```

这条链路避免了两个常见错误：

```text
不能拿旧 revision 的 SourceRef 去读取新 revision 的正文
不能把相似 chunk 的文本直接当成权威证据
```

## 一次文档更新如何使用这些实体

```text
Kafka DocumentReady(version=13)
  -> 构建新的 content_revision = rrev_new
  -> 写入 ContentRevision
  -> 写入 ContentParts
  -> 写入 Sections / ReadingBlocks / SourceRefs
  -> checkpoint.staged = rrev_new
  -> 写入 Qdrant staged vectors 和 ACL
  -> 写入 Neo4j graph projection
  -> checkpoint.applied = rrev_new
  -> checkpoint.staged = null
```

如果任一步失败：

```text
旧 applied revision 继续对查询可见
新 staged revision 等待 Kafka 重试
```

## 最终的职责边界

```text
ContentRevision
    说明“这套 RAG 数据是谁”

Checkpoint
    说明“当前应该读哪套 RAG 数据”

ContentPart
    提供“这套数据对应的完整原文”

Section
    提供“文档结构和导航边界”

ReadingBlock
    提供“Section 内有界的连续阅读内容”

SourceRef
    提供“检索结果到原文证据的精确映射”
```

因此，即使这些实体之间存在 `resource_id`、`document_version`、`content_revision` 或 `section_id` 等重复字段，它们仍然解决不同问题：

```text
版本身份 != 发布指针 != 原文存储 != 文档结构 != 阅读单元 != 证据定位
```
