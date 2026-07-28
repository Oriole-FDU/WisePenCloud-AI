# 标题树主索引

## 目标

让 Agent 像阅读代码仓库一样阅读私有资料：先定位概念，再沿文档内部的标题关系继续读取，最后通过实体关系跳到其他资源。

PageIndex 的关键启发是：标题树不是定位元数据，而是检索和阅读的主索引。先让模型基于标题、路径和摘要选择节点，再按节点 ID 读取正文。

## 数据层级

```text
Document
└─ SectionNode
   ├─ child SectionNode
   └─ SectionReadingBlock
      └─ RetrievalChunk
```

### SectionNode

SectionNode 是稳定的文档语义节点：

```python
SectionNode(
    section_id,
    resource_id,
    document_version,
    title,
    level,
    parent_section_id,
    ordinal,
    section_path,
    summary,
    own_start,
    own_end,
    subtree_end,
)
```

- `own_start..own_end` 是标题到下一个标题的直属正文范围。
- `subtree_end` 是包含后代 Section 的范围。
- 构树只使用 parser 输出的 heading block 和原文 offset，不调用 LLM。
- root 保存第一个标题前的 preface；无标题文档只有 root。
- `summary` 是树检索用的短描述，不替代正文；Contextual Indexing 可用成功的 chunk context 回填它。

### SectionReadingBlock

ReadingBlock 是 Section 内的物理阅读窗口：

- 短 Section 生成一个块。
- 长 Section 按完整 block、page 边界和 hard limit 生成多个有序块。
- 块不能跨 Section；页码只影响物理边界和 locator，不改变 Section 归属。
- 每个块保存 source spans，因此完整内容可以从 Kafka Markdown 确定性回读。

### RetrievalChunk

RetrievalChunk 是检索入口：

- 一个 chunk 只属于一个 ReadingBlock 和一个 Section。
- `raw_text` 用于 evidence；`index_text` 可包含 section path 和 contextual index context。
- Qdrant 只存 RetrievalChunk，不把它当最终阅读上下文。
- 同一 Section 的多个 chunk 命中后，在 evidence materializer 中提升为一个 Section 结果。

## SectionView

SectionView 是查询时构造的读取视图，不是第三种持久化分块：

```text
SectionView
├─ current Section metadata + summary
├─ matched ReadingBlock(s)
├─ exact SourceRef evidence
└─ frontier
   ├─ parent
   ├─ previous / next
   └─ children
```

frontier 只返回 ID、标题、路径、摘要和是否有正文。邻接正文不自动加载，Agent 需要通过
`knowledge_navigate_sections` 指定 Section ID 继续读取。

## 工具边界

- `knowledge_navigate_locate`：混合召回并创建导航状态，返回 SectionView 和跨文档实体节点。
- `knowledge_navigate_sections`：读取指定 Section 的全部 ReadingBlock，并返回下一层标题 frontier。
- `knowledge_navigate_expand`：只沿 Neo4j 跨文档实体关系展开，不接收 Section 专用参数。

标题树边和实体关系边保持两个存储边界，在 `KnowledgeNavigationService` 的导航状态中汇合。

## 验收

- 长 Section 的 ReadingBlock 不跨 Section，且按 ordinal 可完整重建直属正文。
- 多个 RetrievalChunk 命中同一 Section 时只返回一个 SectionView。
- locate 结果能继续调用 sections 读取正文；children/previous/next 返回的 ID 可继续读取。
- frontier 不包含邻接正文 content receipt。
- 图 expand 返回的证据也能通过同一 SectionView 读取，不复制另一套正文上下文结构。
