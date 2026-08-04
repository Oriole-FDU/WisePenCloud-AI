# RAG 服务设计理念

`wisepen-rag-service` 是私有知识库的检索与导航后端。它不保存上游业务文档的权威状态，也不直接暴露 Agent tool；它消费文档、权限和删除事实，在本地维护可检索、可回源、可导航的派生索引，再通过内部 HTTP 接口服务 MCP。

## 服务边界

```text
Java 业务服务
  -> Kafka 文档/ACL/删除事件
  -> wisepen-rag-service
       -> Mongo 内容投影与派生文本
       -> Qdrant 混合检索索引
       -> Neo4j 知识图谱
       -> Redis 导航状态
  -> /internal/rag/knowledge-navigation/*
  -> wisepen-mcp-service 的 RAG tools
```

RAG 服务只对“检索需要的派生状态”负责。原始文档、资源生命周期和权威 ACL 仍属于上游业务服务；MCP tool 的名称、描述和模型可见输出属于 `wisepen-mcp-service`。

## 核心原则

### 证据以原文坐标为准

模型、向量库和图谱都不能成为正文事实源。所有回答证据最终都必须通过 `SourceRef` 回到 Kafka Markdown 的 `source_spans`。Qdrant 命中只说明“这里可能相关”，Neo4j 关系只说明“这里存在可导航关系”，最终正文仍由 Mongo 中的内容投影回源。

### 写入按 revision 发布

内容写入采用 staged -> applied 的发布模型。新投影先完整写入 staging revision；只有 Mongo 内容、Qdrant 索引和相关派生状态准备好后，checkpoint 才切到 applied revision。查询侧只读取 applied revision，因此不会看到半写入状态。

### 检索和阅读分离

`RetrievalChunk` 面向召回、embedding、BM25 和 rerank；`Section` 与 `ReadingBlock` 面向 Agent 阅读。召回命中后会提升到 Section 视图，正文按 ReadingBlock 回读，避免把检索 chunk 当成最终阅读单位。

### 权限前置并 fail closed

ACL 是本服务的本地投影，但不是本服务推断出来的权限。RAG 消费 ACL 重算事件，从上游读取权威 ACL，再同步到 Qdrant 和 Neo4j。召回阶段先按 ACL 过滤，回源和图导航阶段继续校验；权限缺失或 revision 不一致时宁可不返回正文。

### 图谱用于导航，不替代检索

知识图谱从已投影正文中抽取实体和关系，用于跨文档扩展、依赖追踪和关系解释。它不是全文检索引擎，也不替代 Qdrant 的首轮定位。典型流程是先 locate 找到相关 Section，再 cypher 顺着实体关系继续探索。

### LLM 派生物可复用但不覆盖原文

Contextual indexing 和 GraphRAG SDK 抽取结果都按输入指纹持久化复用，避免重复调用模型。它们是派生物缓存，不是权威正文；例如 Section `preview` 保持确定性原文截取，后续如果引入真正的 Section summary，也应作为新字段或新索引层，不覆盖 `preview`。

## 持久化角色

| 后端 | 保存内容 | 角色 |
| --- | --- | --- |
| Mongo | 内容 revision、Markdown parts、Section、ReadingBlock、SourceRef、ACL 投影、LLM 派生缓存 | 回源和版本事实源 |
| Qdrant | RetrievalChunk 向量、BM25 sparse vector、revision、ACL payload、SourceRef 定位字段 | 权限感知混合召回 |
| Neo4j | Resource、Entity、ExternalSource、关系、MENTIONS、ACL payload | 跨文档知识导航 |
| Redis | navigation state | locate/sections/cypher 之间的短期会话状态 |

## 关键不变量

- 每个查询读取唯一 applied content revision。
- `RetrievalChunk`、`Section`、`ReadingBlock`、`SourceRef` 来自同一个内容投影。
- 每个 `RetrievalChunk` 只属于一个 `section_id` 和一个 `reading_block_id`。
- 每个 `ReadingBlock` 的 source span 必须位于所属 Section 的正文范围内。
- `preview` 只来自标题后的直属原文内容，不包含标题，不由 LLM 覆盖。
- `SourceRef.page_labels` 是集合语义，因为一个证据片段可以跨页。
