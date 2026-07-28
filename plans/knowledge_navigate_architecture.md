# `knowledge_navigate` 架构

## 模块

```text
chat/application/rag/
    acl/                    # VIEW 投影与最终授权
    ingestion/              # Kafka Markdown -> Section/Chunk/SourceRef
    retrieval/              # Qdrant candidate + ranking
    evidence/               # applied SourceRef 物化与最终 ACL gate
    section_navigation/     # SectionView 与标题树 frontier
    graph_extraction/       # Neo4j GraphRAG SDK 与 Redis cache
    graph_projection/       # relation revision 编排
    knowledge_navigation.py # locate/expand service 与导航模型
    kafka_consumers.py      # content、ACL、physical delete
    retrieval/locator.py    # retrieval + evidence + SectionView promotion

chat/application/tools/rag_tools/knowledge_navigation/
    locate.py
    sections.py
    expand.py
    common.py

chat/core/persistence/
    mongo/rag_*_projection_repository.py
    qdrant/rag_*_repository.py
    neo4j/knowledge_graph_repository.py
    redis/knowledge_*_repository.py
```

`KnowledgeNavigateLocateTool`、`KnowledgeNavigateSectionsTool` 和 `KnowledgeNavigateExpandTool` 分别处理定位、文档内 Section 读取和跨文档图展开。
`KnowledgeNavigationService` 统一编排召回、树读取、图遍历、证据和状态；持久化留在对应 repository。

## 在线调用

```text
Agent
  -> knowledge_navigate_locate
     -> RagCandidateRetriever: Qdrant hybrid + ranking
     -> SectionNavigator: hit -> SectionView
     -> NodeResolver: RAG hit -> cross-document Entity candidates
     -> Redis state
     -> ToolReturn -> output cache aspect
  -> knowledge_navigate_expand
     -> load Redis state
     -> Neo4j bounded traversal
     -> remote SourceRef -> SectionView
     -> add returned node IDs to state
     -> ToolReturn -> output cache aspect
```

locate 保存原始 query。expand 的可选 query 只记录本轮阅读 focus。

SectionTree 是 Mongo 中按 Resource/version 保存的内容结构投影。它不进入 Neo4j；`locate` 返回命中 Section 和轻量 frontier，
`knowledge_navigate_sections` 按 Section ID 读取正文并继续展开 parent/previous/next/children。具体设计见
[title tree](./knowledge_navigate_title_tree.md)。

Kafka Markdown 到 retrieval leaf 的 paginated/flowing 分流与 source span 契约见
[chunking](./knowledge_navigate_chunking.md)。

## ACL

Java Kafka 事件是 chat-service 的刷新事实源；Resource Mongo 是资源和 ACL 权威数据源。ACL consumer 读取 Resource Mongo，
把 VIEW 投影同步到 Qdrant 和 Neo4j。

查询规则：

- user、group role 和 scope 来自可信调用上下文。
- `locate` 在各召回后端直接应用 `RagPermissionFilterBuilder` 生成的 ACL predicate。
- `expand` 校验每条边的 evidence Resource，并过滤路径中的 Resource endpoint；Entity 只能通过可读 evidence 暴露。
- Qdrant/Neo4j 过滤用于缩小候选；候选、路径和最终正文物化都由 Mongo ACL 投影再次授权。
- `SourceRef` 和 relation count 只在 ACL 过滤后生成。
- 每次 `expand` 使用当前可信 scope 和本地 ACL projection；ACL 缺失或 Mongo 查询失败时 fail closed。

监控 `acl_event_lag` 和 projection update failure。

## 核心模型

```python
class KnowledgeNodeType(StrEnum):
    RESOURCE = "resource"
    ENTITY = "entity"
    EXTERNAL_SOURCE = "external_source"

class RelationOrigin(StrEnum):
    EXTRACTED = "extracted"
    EXPLICIT_REFERENCE = "explicit_reference"
```

`SourceRef` 至少包含：

```text
resource_id, document_version, chunk_id,
source_spans, evidence_start/end,
page_label, section_id, section_path
```

`KnowledgeEdge` 至少包含：

```text
edge_id, source_node_id, target_node_id, relation_type, relation_profile, predicate,
origin, evidence_resource_id, evidence_ref_ids,
extractor_version, source_content_revision, relation_revision, qualifiers
```

Resource 节点引用 Java Resource。所有关系边都绑定提供证据的 Resource 和 source refs。

## Neo4j 投影

```text
(:KnowledgeNode:ResourceNode {
  node_id, resource_id,
  owner_id, readable_users, excluded_read_users,
  content_projection_revision, applied_relation_revision
})

(:ResourceNode)-[:HAS_GROUP_ACL]->(:ResourceGroupAcl {
  resource_id, group_id, is_readable, readable_users, excluded_read_users
})

(:KnowledgeNode:EntityNode {
  node_id, canonical_key, label, entity_type, type_tags
})

(:KnowledgeNode:ExternalSourceNode {
  node_id, source_key, label
})

()-[:KNOWLEDGE_RELATION {
  edge_id, relation_type, relation_profile, predicate, origin,
  evidence_resource_id, evidence_ref_ids,
  extractor_version, qualifiers_json,
  source_content_revision, relation_revision
}]->()
```

查询 Entity-to-Entity 关系时同时 MATCH `evidence_resource_id` 对应的 Resource，并应用其 ACL
predicate；Resource-to-Resource 关系同时过滤目标 Resource。

`relation_type` 使用 core/learning/scholarly profiles 的枚举并保存 profile。writer 按 Resource/relation revision upsert；查询同时要求 evidence Resource 的
`content_projection_revision` 匹配边的 `source_content_revision`，并且 `applied_relation_revision` 匹配边的
`relation_revision`。

物理 edge key 使用 `(source, target, relation_type, predicate, evidence_resource_id, relation_revision)`。同一 Resource 的多个
evidence span 合并为 `evidence_ref_ids`；不同 Resource 的证据分开存边。repository 查询后可按逻辑关系聚合。

## 导航状态

Redis state 只保存后续调用实际使用的数据：

```text
hash nav:{state_id} -> user_id, session_id, root_query
set  nav:{state_id}:known_nodes -> node IDs returned to the Agent
```

约束：

- `state_id` 是不可猜测的 `kns_*` ID，并绑定 user/session。
- `root_query` 供后续 frontier ranking 使用。
- `known_nodes` 同时限制可展开节点并用于结果去重，使用 Redis `SADD` 原子更新。
- 两个 key 使用相同固定 TTL，且不长于 `ToolContentStore` 内容 TTL。

## 失败策略

| 故障                    | 行为                                 |
|-----------------------|------------------------------------|
| Qdrant hybrid query 失败 | 本次入口召回失败，由上层按工具错误处理 |
| Neo4j 失败              | `expand` 失败；不能伪造为空结果               |
| Redis 失败              | 整次调用失败；不降级为无状态导航                   |
| ToolContentStore 写入失败 | 缓存切面跳过失败正文；其他结构和回执继续返回       |
| reranker 失败           | 使用确定性 feature rank                 |
| ACL projection 更新失败   | 保留当前投影并告警，等待现有消费重试                 |
