# 核心功能

RAG 服务围绕三个能力工作：内容索引、权限感知召回、知识导航。ACL 投影贯穿三者，是所有读取能力的安全边界。

## 一、内容索引

内容索引由 `wisepen-document-ready-topic` 的文档完成事件触发。事件携带 `resourceId`、`version` 和 Markdown 正文，服务将它投影成一组可检索、可回源、可导航的数据结构。

```text
DocumentReady
  -> RagContentIndexer
  -> RagSectionProjector
       -> Section
       -> ReadingBlock
       -> RetrievalChunk
       -> SourceRef
  -> Contextual Indexing
  -> Mongo staged revision
  -> Qdrant staged vectors + BM25
  -> Mongo applied checkpoint
  -> KnowledgeGraphIndexer
```

核心产物：

| 产物 | 用途 |
| --- | --- |
| `Section` | 标题树节点，保存标题路径、父子关系、正文范围和原文 preview |
| `ReadingBlock` | Section 内的阅读单位，长 Section 会拆成多个有序 block |
| `RetrievalChunk` | 检索单位，用于 embedding、Qdrant BM25 和 rerank |
| `SourceRef` | 从 chunk 回到原始 Markdown source span 的证据指针 |

Contextual indexing 只增强 `RetrievalChunk.index_text`，不改变 raw text、source span、Section 范围或 evidence 定位。派生 context 按 prompt/model/输入指纹持久化到 Mongo，稳定内容可以跨重试复用。

内容发布通过 checkpoint 控制：写入侧先 stage 完整 revision，再发布为 applied。查询侧只读取 applied revision。

## 二、权限感知召回

召回由内部知识导航接口的 `locate` 触发。请求身份来自服务端安全上下文，而不是客户端 body。RAG 会把用户 ID、群组角色和可管理群组转换成 `RagPermissionScope`，下推到 Qdrant 查询和后续回源。

```text
locate(query)
  -> query embedding
  -> Qdrant dense + native BM25
  -> ACL filter
  -> applied revision filter
  -> ranking
  -> SourceRef / ReadingBlock 回源
  -> SectionView
```

召回阶段返回的是候选 chunk，但服务不会把 chunk 直接当最终答案上下文。命中会按 Section 聚合，保留排名最高的入口，再回源 ReadingBlock 和 SourceRef，形成包含正文证据和标题树 frontier 的 `SectionView`。

`SectionView` 由几部分组成：

| 字段 | 含义 |
| --- | --- |
| 当前 Section | 命中或读取的标题树节点 |
| `reading_blocks` | 当前 Section 内可读正文 |
| `evidence` | SourceRef 回源后的精确证据 |
| `frontier` | parent、previous、next、children 的轻量结构信息 |

frontier 只帮助 Agent 决定下一步读哪里，不预加载相邻正文。完整正文必须通过 `sections` 按 Section ID 再读。

## 三、知识导航

知识导航让 Agent 在私有资料中逐步阅读，而不是一次性塞入大量上下文。它由三个动作组成：

| 动作 | 作用 |
| --- | --- |
| `locate` | 用自然语言问题找到相关 Section，并创建导航状态 |
| `sections` | 读取已发现 Section 的完整 ReadingBlock，并扩展标题树 frontier |
| `expand` | 从已发现图节点出发，在 Neo4j 中做有界关系遍历 |

`locate` 创建 Redis navigation state，记录用户、会话、初始问题、已展示图节点和已发现 Section。后续 `sections` 和 `expand` 必须携带同一个 `state_id` 与 `session_id`，并且只能请求已经出现在当前状态中的 Section 或节点；否则返回状态失效错误。

知识图谱写入由内容索引后续触发：

```text
applied content revision
  -> SourceRef 顺序重建抽取窗口
  -> GraphRAG SDK 抽取候选节点/关系
  -> evidence quote 按原文 offset 校验
  -> Neo4j projection
  -> MENTIONS 连接 RetrievalChunk 和知识节点
```

图谱关系必须带原文证据。`expand` 返回关系边、路径和 evidence 来源 Section；Agent 可以用这些来源继续读取正文，而不是只相信关系标签。

## 删除与重建

资源删除事件会清理 Mongo、Qdrant、Neo4j 中的 RAG 派生状态，并删除导航可见的 ACL/内容索引。ACL 重算事件会刷新本地 ACL 投影，再同步到 Qdrant 和 Neo4j。两类事件都只处理派生数据，不删除上游业务资源。
