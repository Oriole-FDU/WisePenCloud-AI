# RAG 代码导读

## 核心模型

```text
Kafka Markdown
  -> SectionNode 标题树
  -> SectionReadingBlock（单个 Section 内的有界正文）
  -> RetrievalChunk（dense/BM25/reranker 入口）
  -> SourceRef（Kafka 正文精确证据）
```

- `SectionNode` 是文档内部的语义节点，负责标题路径、父子关系、摘要和正文范围。
- `SectionReadingBlock` 只解决一个 Section 过长时的上下文预算，不跨 Section。
- `RetrievalChunk` 只负责召回；命中后提升回 Section 和 ReadingBlock，不在 Mongo 再保存一份副本。
- `SourceRef` 是正文和权限回源的唯一证据入口。
- Neo4j 只保存跨文档实体关系，不保存标题树。

## 写入链路

```text
Java Kafka DocumentReady
  -> RagDocumentReadyConsumer
  -> RagContentIndexer
       -> RagSectionProjector
       -> ContextIndexingService
       -> Mongo staged projection
       -> Qdrant dense + qdrant/bm25
       -> Mongo applied revision
  -> KnowledgeGraphIndexer
       -> retrieval chunk 抽取窗口
       -> Neo4j GraphRAG SDK
```

Section 树先于检索块构建。每个 Section 的 `own_start..own_end` 单独装配 ReadingBlock；长 Section 产生多个有序块，短
Section 只有一个块。Contextual Indexing 只修改 `index_text`，不修改 Section、ReadingBlock、raw text 或 SourceRef。

## 查询链路

```text
knowledge_navigate_locate
  -> Qdrant dense/BM25 native RRF
  -> applied revision + ACL
  -> Section 归并
  -> ReadingBlock 和 SourceRef 回源
  -> SectionView（当前节点 + 轻量 frontier）

knowledge_navigate_sections
  -> state 校验
  -> ACL 二次授权
  -> 按 Section ID 读取全部 ReadingBlock
  -> 返回 parent/previous/next/children frontier

knowledge_navigate_expand
  -> Neo4j 跨文档实体关系有界遍历
  -> SourceRef 回源
  -> SectionView
```

`SectionView` 不预加载邻接正文，只返回节点 ID、标题、路径、摘要和可展开关系。正文通过当前 Section 的 ReadingBlock 按需读取，
大正文继续由 Tool Content API 负责缓存和窗口读取。

## 目录职责

| 目录 | 作用 |
|---|---|
| `ingestion/models.py` | Kafka 内容、SectionNode、ReadingBlock、RetrievalChunk、SourceRef |
| `ingestion/section_tree.py` | 从 parser blocks 确定性构建标题树 |
| `ingestion/section_projector.py` | Section -> ReadingBlock -> RetrievalChunk 投影 |
| `ingestion/context_indexing.py` | 生成检索上下文并回填 Section 摘要 |
| `retrieval/` | Qdrant 召回、版本过滤、排序 |
| `evidence/` | SourceRef 和 ReadingBlock 权威回源、ACL 二次授权 |
| `section_navigation/` | SectionView 和标题树 frontier |
| `knowledge_navigation.py` | locate、Section 读取、跨文档图展开编排 |
| `graph_extraction/` | 从检索子块构造 LLM 抽取窗口 |
| `graph_projection/` | 图结果去重、revision 和 Neo4j 投影 |
| `repositories/` | RAG 仓储协议统一入口 |

## 代码阅读顺序

1. `ingestion/section_tree.py`、`ingestion/section_projector.py`、`ingestion/models.py`
2. `ingestion/context_indexing.py`、`content_indexer.py`、`revision.py`
3. `retrieval/retriever.py`、`evidence/materializer.py`、`section_navigation/`
4. `knowledge_navigation.py` 和 `tools/rag_tools/knowledge_navigation/`
5. `graph_extraction/`、`graph_projection/`、Neo4j repository

## 关键不变量

- 每个 RetrievalChunk 只属于一个 `section_id` 和一个 `reading_block_id`。
- 每个 ReadingBlock 的所有 source span 都位于所属 Section 的 `own` 范围内。
- Qdrant RetrievalChunk 与 Mongo Section、ReadingBlock、SourceRef 使用同一个 revision staged/applied 切换。
- 命中同一 Section 的多个检索子块只保留排名最高的 Section 结果。
- Section frontier 只携带结构信息，不携带邻接正文。
- 所有正文都通过 Kafka Markdown 的 source span 回读，不信任模型生成的 offset。

仓储协议、Mongo 实体和具体后端的职责见 [`repositories/README.md`](repositories/README.md)。

核心数据模型的生产者、消费者和生命周期见 [`data_models.md`](data_models.md)。

内容投影实体为什么存在、如何协作见 [`content_projection_entities.md`](content_projection_entities.md)。
