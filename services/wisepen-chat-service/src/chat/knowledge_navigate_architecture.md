# `knowledge_navigate` 架构

## 模块

```text
chat/application/knowledge_navigation/
    models.py
    navigator.py
    locator.py
    traverser.py
    node_resolver.py
    section_context_builder.py
    frontier_ranker.py
    result_builder.py
    repository_protocols.py
    indexing/
        document_structure_builder.py
        retrieval_chunk_builder.py
        section_tree_builder.py
        learning_relation_extractor.py
        concept_resolver.py
        evidence_validator.py
        projection_models.py

chat/application/tools/knowledge_navigation/
    knowledge_navigate_tool.py
    action_check.py

chat/core/persistence/neo4j/
    knowledge_navigation_repository.py

chat/core/persistence/redis/
    knowledge_navigation_state_repository.py
```

`KnowledgeNavigateTool` 只处理公开 schema、可信上下文和错误映射。`KnowledgeNavigator` 负责编排；召回、遍历、状态和持久化分别留在对应模块。

## 在线调用

```text
Agent
  -> knowledge_navigate
     -> locate
        -> RagCandidateRetriever: ES + Qdrant + ranking
        -> SectionContextBuilder: hit -> local title context
        -> NodeResolver: RAG hit -> cross-document Concept candidates
        -> SourceRef -> ToolContentStore
        -> Redis state
     -> expand
        -> load Redis state
        -> Neo4j bounded traversal
        -> relation-aware ranking
        -> remote SourceRef -> SectionContextBuilder
        -> SourceRef -> ToolContentStore
        -> add returned node IDs to state
```

`locate` 保存原始 query。`expand` 的可选 query 只参与本轮排序。

SectionTree 是 Mongo 中按 Resource/version 保存的内容结构投影。它不进入 Neo4j；`locate` 和远端 `expand` 使用同一个
`SectionContextBuilder` 恢复祖先导语、前一个同级章节和直接子章节。具体设计见
[title tree](./knowledge_navigate_title_tree.md)。

Kafka Markdown 到 retrieval leaf 的 paginated/flowing 分流与 source span 契约见
[chunking](./knowledge_navigate_chunking.md)。

## ACL

Java Kafka 是 chat-service 的 Resource/ACL 权威事实源。沿用现有 ACL consumer、projector 和 updater，把读取权限同步到
ES、Qdrant 和 Neo4j。

查询规则：

- user、group role 和 scope 来自可信调用上下文。
- `locate` 在各召回后端直接应用 `RagPermissionFilterBuilder` 生成的 ACL predicate。
- `expand` 对起点、中间节点和终点逐节点过滤；任一节点不可读则整条路径不存在。
- `SourceRef` 和 relation count 只在 ACL 过滤后生成。
- 每次 `expand` 使用当前可信 scope 和本地 ACL projection。

监控 `acl_event_lag` 和 projection update failure。

## 核心模型

```python
class KnowledgeNodeType(StrEnum):
    RESOURCE = "resource"
    CONCEPT = "concept"
    EXTERNAL_SOURCE = "external_source"

class RelationOrigin(StrEnum):
    EXTRACTED = "extracted"
    EXPLICIT_REFERENCE = "explicit_reference"
```

`SourceRef` 至少包含：

```text
resource_id, document_version, chunk_id,
source_spans, evidence_start/end, content_ref, content_start/end,
page_label, section_id, section_path
```

`KnowledgeEdge` 至少包含：

```text
edge_id, source_node_id, target_node_id, relation_type,
origin, evidence_resource_id, evidence_ref_ids,
extractor_version, source_content_revision, relation_revision, qualifiers
```

Resource 节点引用 Java Resource。学习边绑定提供证据的 Resource 和 source refs。

## Neo4j 投影

```text
(:KnowledgeNode:ResourceNode {
  node_id, resource_id,
  owner_id, readable_users, computed_group_acls,
  content_projection_revision, applied_relation_revision
})

(:KnowledgeNode:ConceptNode {
  node_id, canonical_key, label
})

(:KnowledgeNode:ExternalSourceNode {
  node_id, source_key, label
})

()-[:KNOWLEDGE_RELATION {
  edge_id, relation_type, origin,
  evidence_resource_id, evidence_ref_ids,
  extractor_version, qualifiers_json,
  source_content_revision, relation_revision
}]->()
```

查询 Concept-to-Concept 关系时同时 MATCH `evidence_resource_id` 对应的 Resource，并应用其 ACL
predicate；Resource-to-Resource 关系同时过滤目标 Resource。

`relation_type` 使用枚举属性。writer 按 Resource/relation revision upsert；查询同时要求 evidence Resource 的
`content_projection_revision` 匹配边的 `source_content_revision`，并且 `applied_relation_revision` 匹配边的
`relation_revision`。

物理 edge key 使用 `(source, target, relation_type, evidence_resource_id, relation_revision)`。同一 Resource 的多个
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
| ES/Qdrant 某召回通道失败     | 使用仍可用通道；全部失败才报 backend unavailable |
| Neo4j 失败              | `expand` 失败；不能伪造为空结果               |
| Redis 失败              | 整次调用失败；不降级为无状态导航                   |
| ToolContentStore 写入失败 | 不返回悬空 `content_ref`                |
| reranker 失败           | 使用确定性 feature rank                 |
| ACL projection 更新失败   | 保留当前投影并告警，等待现有消费重试                 |
