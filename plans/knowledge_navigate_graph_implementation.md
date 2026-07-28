# 图关系抽取与检索落地

## 技术结论

生产链路使用两套 Neo4j 官方 Python 包：

| 依赖                       |     锁定版本 | 用途                               |
|--------------------------|---------:|----------------------------------|
| `neo4j-graphrag`         | `1.18.0` | 用 LLM 从正文窗口抽取通用实体和 profile 关系      |
| `neo4j`                  |  `6.2.0` | 写入跨文档图并执行一至两跳 Cypher             |
| `openai`                 | `2.x`    | 由现有 `QueryClient` 使用，不引入冲突的 SDK extra |
| `pydantic`               | `2.13.4` | SDK structured output 和领域 DTO 校验 |

版本取自原 RAG 仓库 `uv.lock`。在 `formal_pr` 增加前两项依赖后重新生成 lock。

抽取只使用 `neo4j-graphrag` 的组件 API，不让完整 KG pipeline 接管 Kafka 正文、切块和写库：

```text
Kafka content -> source-backed retrieval leaf/window
              -> LLMEntityRelationExtractor
              -> Neo4jGraph（候选图）
              -> EvidenceValidator + EntityResolver
              -> 领域节点/边
              -> neo4j AsyncDriver
```

这样 SDK 负责 LLM prompt 编排、版本化 schema、并发调用、structured output 和输出建模；WisePen 负责 SourceRef、canonical Entity、resource revision、ACL 和增量替换。

## 使用的公开 API

| API                                                        | 在本方案中的职责                                      |
|------------------------------------------------------------|-----------------------------------------------|
| `neo4j_graphrag.llm.base.LLMInterfaceV2`                   | 接入现有 `QueryClient`，向 SDK 提供 structured output |
| `LLMEntityRelationExtractor(...)`                          | 配置抽取器、并发数、错误语义和 lexical graph 开关              |
| `LLMEntityRelationExtractor.run(...)`                      | 输入 `TextChunks + GraphSchema`，返回 `Neo4jGraph` |
| `GraphSchema`、`NodeType`、`RelationshipType`、`PropertyType` | 限定可抽取节点、关系、端点组合和 evidence 字段                  |
| `TextChunk`、`TextChunks`                                   | 把已有增量抽取窗口交给 SDK                               |
| `Neo4jGraph`、`Neo4jNode`、`Neo4jRelationship`               | SDK 与领域校验层之间的候选结果契约                           |
| `AsyncGraphDatabase.driver(...)`                           | 创建进程级异步 Neo4j driver                          |
| `AsyncDriver.verify_connectivity()`                        | 启动时验证数据库连接                                    |
| `AsyncDriver.execute_query(...)`                           | 执行约束、批量 upsert、revision 清理和有界遍历               |
| `RoutingControl.READ`                                      | 将纯检索查询路由到 reader                              |

`neo4j-graphrag` 官方仍把 KG Builder 标为 experimental，因此把所有 import 和输出转换集中在
`knowledge_graph_extractor.py`，并固定 `1.18.x`；升级时只需跑该适配层的 contract fixture。

## 抽取 schema

schema 使用固定的通用实体类型，并按 `core/learning/scholarly` profile 组合关系集合，不从每篇文档动态生成。`Resource` 表示当前私有正文；文中出现的论文、产品、人物等先抽取为通用 `Entity`，引用目标再解析为已有 Resource 或 ExternalSource。

```python
from neo4j_graphrag.experimental.components.schema import (
    GraphSchema,
    NodeType,
    PropertyType,
    RelationshipType,
)

ENTITY_TYPES = {
    "concept", "person", "organization", "product", "technology",
    "method", "dataset", "event", "place", "document", "other",
}

CORE_RELATIONS = {
    "ABOUT": "主体内容明确围绕客体",
    "RELATED_TO": "主体与客体存在 evidence 中描述的显式关系",
    "PART_OF": "主体是客体的组成部分",
    "USES": "主体明确使用客体",
    "PRODUCES": "主体明确产生客体",
    "DEPENDS_ON": "主体依赖客体",
    "DERIVED_FROM": "主体明确来源于客体",
    "IMPLEMENTS": "主体实现客体",
    "APPLIES_TO": "主体适用于客体",
    "CAUSES": "主体导致客体",
    "COMPARES_WITH": "正文明确比较主体和客体",
    "CONTRADICTS": "正文明确指出主体和客体冲突",
    "EXTENDS": "主体扩展客体",
    "SUPERSEDES": "主体替代客体",
    "LOCATED_IN": "主体位于客体地点",
    "AUTHORED_BY": "主体由客体人物或组织创作",
}

PROFILE_RELATIONS = {
    "learning": {
        "DEFINES": "主体给出客体的正式定义",
        "EXPLAINS": "主体解释或推导客体",
        "EXAMPLE_OF": "主体是客体的实例",
        "REQUIRES": "理解或使用主体需要客体",
    },
    "scholarly": {
        "CITES": "主体明确引用客体来源",
        "PUBLISHED_IN": "主体发表于客体",
        "USES_DATASET": "主体使用客体数据集",
        "USES_METHOD": "主体使用客体方法",
        "SUPPLEMENTS": "主体补充客体文档",
        "RETRACTS": "主体撤回客体文档",
    },
}


def build_knowledge_graph_schema(enabled_profiles: set[str]) -> GraphSchema:
    relation_descriptions = dict(CORE_RELATIONS)
    for profile in enabled_profiles:
        if profile != "core":
            relation_descriptions.update(PROFILE_RELATIONS[profile])

    evidence_properties = [
        PropertyType(
            name="evidence_quote",
            type="STRING",
            description="CURRENT_CHUNK 中支持该关系的连续原文",
            required=True,
        ),
        PropertyType(
            name="assertion",
            type="STRING",
            description="affirmed、negated、conditional 或 uncertain",
            required=True,
        ),
        PropertyType(
            name="predicate",
            type="STRING",
            description="RELATED_TO 的具体谓词，其他关系可为空",
        ),
    ]
    relationship_types = [
        RelationshipType(
            label=label,
            description=description,
            properties=evidence_properties,
        )
        for label, description in relation_descriptions.items()
    ]
    return GraphSchema(
        node_types=[
            NodeType(
                label="Entity",
                description="正文中的通用实体，可表示概念、人物、组织、产品、技术、方法、数据集、事件、地点或文档",
                properties=[
                    PropertyType(name="name", type="STRING", required=True),
                    PropertyType(
                        name="entity_type",
                        type="STRING",
                        description=f"实体类型，取值为 {sorted(ENTITY_TYPES)}",
                        required=True,
                    ),
                    PropertyType(
                        name="evidence_quote",
                        type="STRING",
                        description="CURRENT_CHUNK 中出现该实体的连续原文",
                        required=True,
                    ),
                ],
            ),
            NodeType(
                label="Resource",
                description="由系统注入的 CURRENT_RESOURCE",
                properties=[PropertyType(name="name", type="STRING", required=True)],
            ),
            NodeType(
                label="ExternalSource",
                description="正文明确引用、但不能解析为私有 Resource 的来源",
                properties=[PropertyType(name="name", type="STRING", required=True)],
            ),
        ],
        relationship_types=relationship_types,
        patterns=[
            *(('Entity', relation, 'Entity') for relation in relation_descriptions),
            *(('Resource', relation, 'Entity') for relation in (
                'ABOUT', 'AUTHORED_BY', 'DEFINES', 'EXPLAINS', 'EXAMPLE_OF'
            ) if relation in relation_descriptions),
            *(('Resource', 'CITES', target) for target in (
                'Resource', 'Entity', 'ExternalSource'
            ) if 'CITES' in relation_descriptions),
            *(('Entity', 'CITES', target) for target in (
                'Entity', 'ExternalSource'
            ) if 'CITES' in relation_descriptions),
            ('Entity', 'DERIVED_FROM', 'ExternalSource'),
        ],
        additional_node_types=False,
        additional_relationship_types=False,
        additional_patterns=False,
    )
```

`evidence_quote` 是抽取结果进入图的硬门槛。模型只负责返回 quote；字符位置由程序在 `CURRENT_CHUNK` 中重新定位，避免相信 LLM
生成的 offset。

`MENTIONS` 不进入 LLM 的 `relationship_types`。Entity mention 通过 evidence 校验并完成 canonical 解析后，由 projection writer 确定性创建 `Resource -> Entity` 的 `MENTIONS` 边。`relation_profile` 也由 relation type 注册表推导；模型只输出无法从 schema 推导的 `predicate` 和 assertion。

## 调用抽取 SDK

一个 `TextChunk` 对应一个增量抽取窗口。相邻内容只提供消歧上下文，所有可写入证据必须来自 `CURRENT_CHUNK`。

```python
from neo4j_graphrag.experimental.components.entity_relation_extractor import (
    LLMEntityRelationExtractor,
    OnError,
)
from neo4j_graphrag.experimental.components.types import TextChunk, TextChunks
from chat.application.rag.graph_extraction import QueryClientGraphRagLLM
from chat.application.utils.llm_clients import build_query_client

llm = QueryClientGraphRagLLM(client=build_query_client())
extractor = LLMEntityRelationExtractor(
    llm=llm,
    create_lexical_graph=False,
    on_error=OnError.RAISE,
    max_concurrency=5,
    use_structured_output=True,
)

graph = await extractor.run(
    chunks=TextChunks(
        chunks=[
            TextChunk(
                uid=window.chunk_id,
                index=window.chunk_index,
                text=render_extraction_window(window),
                metadata={
                    "resource_id": window.resource_id,
                    "content_revision": window.content_revision,
                },
            )
        ]
    ),
    schema=build_knowledge_graph_schema(),
    examples=KNOWLEDGE_RELATION_EXAMPLES,
)
```

`use_structured_output=True` 使 SDK 把 `Neo4jGraph` Pydantic 类型作为 `response_format` 交给 LLM，返回内容再由 SDK 执行
`Neo4jGraph.model_validate_json(...)`。`OnError.RAISE` 让失败窗口进入关系抽取任务重试，而不是把抽取失败当成“没有关系”。

`KNOWLEDGE_RELATION_EXAMPLES` 先覆盖人物、组织、产品、技术、方法、数据集和文档引用，再补定义、前置知识等学习关系。示例还要包含否定关系、`RELATED_TO` predicate 和空结果，不使用代码 AST 或特定学科字段。

## 从候选图到可写关系

SDK 返回的 node ID 只是本次抽取内的局部 ID。转换顺序如下：

```text
Neo4jGraph local node ID
  -> evidence_quote 精确定位
  -> Resource endpoint 绑定当前 resource 或解析引用
  -> Entity mention 交给 EntityResolver
  -> canonical KnowledgeEntity node_id
  -> SourceRef
  -> KnowledgeEdge
```

证据定位核心代码：

```python
from dataclasses import dataclass

from neo4j_graphrag.experimental.components.types import Neo4jGraph


@dataclass(frozen=True)
class EvidenceSpan:
    quote: str
    start: int
    end: int


def locate_quote(current_chunk: str, quote: object) -> EvidenceSpan | None:
    if not isinstance(quote, str) or not quote.strip():
        return None
    start = current_chunk.find(quote)
    if start < 0:
        return None
    return EvidenceSpan(quote=quote, start=start, end=start + len(quote))


def validated_relationships(graph: Neo4jGraph, current_chunk: str):
    nodes = {node.id: node for node in graph.nodes}
    for relationship in graph.relationships:
        source = nodes.get(relationship.start_node_id)
        target = nodes.get(relationship.end_node_id)
        evidence = locate_quote(
            current_chunk,
            relationship.properties.get("evidence_quote"),
        )
        if source is None or target is None or evidence is None:
            continue
        yield source, relationship, target, evidence
```

随后按 [relations](./knowledge_navigate_relations.md) 的 endpoint matrix 校验关系方向，把 `EvidenceSpan` 与当前 chunk
locator 合成为 `SourceRef`。同名 Entity 不在此处直接合并；`EntityResolver` 用 entity type、稳定标识和当前 knowledge scope 召回 canonical candidates，再根据局部证据绑定。

`neo4j-graphrag` 自带 `SinglePropertyExactMatchResolver`、`FuzzyMatchResolver` 和 `SpaCySemanticMatchResolver`，这些 API
会直接合并 Neo4j 中的实体。这里不调用它们，因为 canonical Entity 必须受 knowledge scope、实体类型、证据和 resource 增量边界约束；SDK
的职责到候选图结束。

## Neo4j 图写入

所有节点同时带公共标签 `KnowledgeNode`，再带一个类型标签，便于用全局 `node_id` 定位边端点：

```cypher
CREATE CONSTRAINT knowledge_node_id IF NOT EXISTS
FOR (node:KnowledgeNode) REQUIRE node.node_id IS UNIQUE;

CREATE CONSTRAINT resource_group_acl_id IF NOT EXISTS
FOR (acl:ResourceGroupAcl) REQUIRE acl.acl_id IS UNIQUE;
```

进程启动时创建并验证 driver：

```python
from neo4j import AsyncGraphDatabase

driver = AsyncGraphDatabase.driver(
    settings.NEO4J_URI,
    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
)
await driver.verify_connectivity()
```

Entity 节点和关系批量 upsert：

```python
UPSERT_ENTITIES = """
UNWIND $entities AS entity
MERGE (node:KnowledgeNode:EntityNode {node_id: entity.node_id})
SET node.canonical_key = entity.canonical_key,
    node.label = entity.label,
    node.entity_type = entity.entity_type,
    node.type_tags = entity.type_tags
"""

UPSERT_RELATIONS = """
UNWIND $relations AS item
MATCH (source:KnowledgeNode {node_id: item.source_node_id})
MATCH (target:KnowledgeNode {node_id: item.target_node_id})
MERGE (source)-[relation:KNOWLEDGE_RELATION {edge_id: item.edge_id}]->(target)
SET relation.relation_type = item.relation_type,
    relation.relation_profile = item.relation_profile,
    relation.predicate = item.predicate,
    relation.origin = item.origin,
    relation.evidence_resource_id = item.evidence_resource_id,
    relation.evidence_ref_ids = item.evidence_ref_ids,
    relation.extractor_version = item.extractor_version,
    relation.qualifiers_json = item.qualifiers_json,
    relation.source_content_revision = item.source_content_revision,
    relation.relation_revision = item.relation_revision
"""


async def write_projection(driver, database, entities, relations) -> None:
    await driver.execute_query(
        UPSERT_ENTITIES,
        entities=entities,
        database_=database,
    )
    await driver.execute_query(
        UPSERT_RELATIONS,
        relations=relations,
        database_=database,
    )
```

ResourceNode 和 ExternalSourceNode 分别用同样的 `UNWIND + MERGE` 写入。关系 evidence 全部校验后，把 ResourceNode 的
`applied_relation_revision` 切到新 revision；随后清理该 Resource 的旧证据边：

```cypher
MATCH ()-[relation:KNOWLEDGE_RELATION]->()
WHERE relation.evidence_resource_id = $resource_id
  AND relation.relation_revision <> $applied_relation_revision
DELETE relation
```

不直接使用 `Neo4jWriter.run(...)`：它写的是 SDK 局部实体图，无法在写前完成 canonical Entity、SourceRef、ACL evidence
Resource、content revision 和 relation revision 的绑定。SDK 输出经过领域转换后统一由 repository 写入。

## 一至两跳图检索

`locate` 先通过 RAG hit 的 `(resource_id, version, chunk_id)` 查 Mongo chunk-to-entity projection，得到 `seed_node_ids`。
`expand` 再从这些 Entity IDs 按启用 profile 的关系集合做一至两跳展开。

方向只从枚举映射到常量 pattern：

```python
PATH_PATTERNS = {
    ("outgoing", 1): "(seed)-[:KNOWLEDGE_RELATION*1]->(target)",
    ("outgoing", 2): "(seed)-[:KNOWLEDGE_RELATION*1..2]->(target)",
    ("incoming", 1): "(seed)<-[:KNOWLEDGE_RELATION*1]-(target)",
    ("incoming", 2): "(seed)<-[:KNOWLEDGE_RELATION*1..2]-(target)",
    ("both", 1): "(seed)-[:KNOWLEDGE_RELATION*1]-(target)",
    ("both", 2): "(seed)-[:KNOWLEDGE_RELATION*1..2]-(target)",
}
```

repository 复用现有 `RagPermissionFilterBuilder.build_neo4j_predicate(...)`。每条边检查其 evidence Resource 的 content
revision、applied relation revision 和 ACL；路径中的 Resource endpoint 也检查 ACL。

Neo4j 属性不能保存嵌套 map，Resource 的群组 ACL 使用 `(ResourceNode)-[:HAS_GROUP_ACL]->(ResourceGroupAcl)` 投影；owner、指定允许用户和指定拒绝用户保留在 ResourceNode。permission builder 用 `EXISTS` 子查询检查 ACL node，与 Qdrant nested payload 保持同一 `VIEW` 语义。

```python
from neo4j import AsyncDriver, RoutingControl


class KnowledgeNavigationRepository:
    def __init__(self, *, driver: AsyncDriver, database: str, permission_filter_builder):
        self._driver = driver
        self._database = database
        self._permission_filter_builder = permission_filter_builder

    async def expand(
            self,
            *,
            seed_node_ids: tuple[str, ...],
            relation_types: tuple[str, ...],
            direction: str,
            depth: int,
            permission_scope,
            limit: int,
    ):
        pattern = PATH_PATTERNS[(direction, depth)]
        evidence_acl, acl_params = self._permission_filter_builder.build_neo4j_predicate(
            permission_scope,
            node_alias="evidence",
        )
        endpoint_acl, _ = self._permission_filter_builder.build_neo4j_predicate(
            permission_scope,
            node_alias="resource",
        )
        query = f"""
        MATCH path={pattern}
        WHERE seed.node_id IN $seed_node_ids
          AND all(relation IN relationships(path)
            WHERE relation.relation_type IN $relation_types
              AND EXISTS {{
                MATCH (evidence:ResourceNode)
                WHERE evidence.resource_id = relation.evidence_resource_id
                  AND evidence.content_projection_revision = relation.source_content_revision
                  AND evidence.applied_relation_revision = relation.relation_revision
                  AND {evidence_acl}
              }})
          AND all(resource IN [node IN nodes(path) WHERE node:ResourceNode]
            WHERE {endpoint_acl})
        WITH DISTINCT path
        ORDER BY length(path), target.node_id
        LIMIT $path_scan_limit
        RETURN [node IN nodes(path) | properties(node)] AS nodes,
               [relation IN relationships(path) | properties(relation)] AS relations,
               length(path) AS depth
        """
        records, _, _ = await self._driver.execute_query(
            query,
            seed_node_ids=list(seed_node_ids),
            relation_types=list(relation_types),
            path_scan_limit=max(limit * 5, limit),
            database_=self._database,
            routing_=RoutingControl.READ,
            **acl_params,
        )
        return records
```

repository 返回候选 path 后，`frontier_ranker` 用 root query、relation type、路径深度和 evidence 相关性排序，截取 `limit`
个完整 path。结果构造阶段批量读取 `evidence_ref_ids` 对应的 SourceRef，生成 preview 和 tool output `content_index`，Agent 再决定沿哪个 Entity 继续读。

## 最小验证集

1. schema/structured output：每种关系有正例，端点错误被清理，结果可由 `Neo4jGraph` 校验。
2. evidence：quote 不在 `CURRENT_CHUNK` 时不生成 SourceRef 和边。
3. 跨文档：不同 Resource 中同一人物、产品、方法或概念 mention 绑定同一 canonical Entity。
4. 增量：修改一个 chunk 只替换该 Resource、该 revision 的证据边。
5. ACL：任一路径边的 evidence Resource 不可读时，整条 path 不返回。
6. 遍历：分别验证 core、learning、scholarly 关系的方向、depth 1 和 depth 2 完整路径。

## 官方资料

- [Neo4j GraphRAG Knowledge Graph Builder](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html)
- [Neo4j GraphRAG API: LLMEntityRelationExtractor](https://neo4j.com/docs/neo4j-graphrag-python/current/api.html#neo4j_graphrag.experimental.components.entity_relation_extractor.LLMEntityRelationExtractor)
- [Neo4j GraphRAG types](https://neo4j.com/docs/neo4j-graphrag-python/current/types.html)
- [Neo4j Python Driver 6.2 async API](https://neo4j.com/docs/api/python-driver/current/async_api.html)
- [Neo4j Cypher EXISTS subqueries](https://neo4j.com/docs/cypher-manual/current/subqueries/existential/)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
