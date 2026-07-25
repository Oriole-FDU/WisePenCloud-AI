# 图关系抽取与检索落地

## 技术结论

生产链路使用两套 Neo4j 官方 Python 包：

| 依赖                       |     锁定版本 | 用途                               |
|--------------------------|---------:|----------------------------------|
| `neo4j-graphrag[openai]` | `1.18.0` | 用 LLM 从正文窗口抽取实体和固定学习关系           |
| `neo4j`                  |  `6.2.0` | 写入跨文档图并执行一至两跳 Cypher             |
| `openai`                 | `2.41.1` | `OpenAILLM` 的模型客户端，由 extra 引入    |
| `pydantic`               | `2.13.4` | SDK structured output 和领域 DTO 校验 |

版本取自原 RAG 仓库 `uv.lock`。在 `formal_pr` 增加前两项依赖后重新生成 lock。

抽取只使用 `neo4j-graphrag` 的组件 API，不让完整 KG pipeline 接管 Kafka 正文、切块和写库：

```text
Kafka content -> source-backed retrieval leaf/window
              -> LLMEntityRelationExtractor
              -> Neo4jGraph（候选图）
              -> EvidenceValidator + ConceptResolver
              -> 领域节点/边
              -> neo4j AsyncDriver
```

这样 SDK 负责 LLM prompt 编排、固定 schema、并发调用、structured output 和输出建模；WisePen 负责 SourceRef、canonical
Concept、resource revision、ACL 和增量替换。

## 使用的公开 API

| API                                                        | 在本方案中的职责                                      |
|------------------------------------------------------------|-----------------------------------------------|
| `neo4j_graphrag.llm.OpenAILLM`                             | 创建支持 Pydantic structured output 的 LLM 客户端     |
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
`learning_relation_extractor.py`，并固定 `1.18.x`；升级时只需跑该适配层的 contract fixture。

## 抽取 schema

schema 固定学习关系集合，不从每篇文档动态生成。`Resource` 表示当前文档或正文中明确引用的文档；后处理会把当前文档强制绑定到
Kafka `resource_id`，引用目标再解析为已有 Resource 或 ExternalSource。

```python
from neo4j_graphrag.experimental.components.schema import (
    GraphSchema,
    NodeType,
    PropertyType,
    RelationshipType,
)

CONCEPT_RELATIONS = {
    "REQUIRES": "理解或使用主体概念以前置概念为条件",
    "DERIVED_FROM": "主体概念明确来源于客体",
    "EXTENDS": "主体概念在客体基础上增加内容",
    "APPLIES_TO": "主体方法或概念明确应用于客体",
    "CONTRASTS_WITH": "正文明确比较两个概念",
    "CONTRADICTS": "正文明确指出两个概念冲突",
    "SUPERSEDES": "主体概念明确替代客体",
}

RESOURCE_RELATIONS = {
    "DEFINES": "当前 Resource 给出 Concept 的正式定义",
    "EXPLAINS": "当前 Resource 解释或推导 Concept",
    "EXAMPLE_OF": "当前 Resource 给出 Concept 的实例",
    "CITES": "当前 Resource 明确引用另一个来源",
}


def build_learning_graph_schema() -> GraphSchema:
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
    ]
    relationship_types = [
        RelationshipType(
            label=label,
            description=description,
            properties=evidence_properties,
        )
        for label, description in (CONCEPT_RELATIONS | RESOURCE_RELATIONS).items()
    ]
    return GraphSchema(
        node_types=[
            NodeType(
                label="Concept",
                description="正文中可跨文档复用的知识概念、方法、原理或问题",
                properties=[
                    PropertyType(name="name", type="STRING", required=True),
                    PropertyType(
                        name="evidence_quote",
                        type="STRING",
                        description="CURRENT_CHUNK 中出现该概念的连续原文",
                        required=True,
                    ),
                ],
            ),
            NodeType(
                label="Resource",
                description="CURRENT_RESOURCE 或正文明确引用的文档",
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
            *(('Resource', relation, 'Concept') for relation in (
                'DEFINES', 'EXPLAINS', 'EXAMPLE_OF'
            )),
            *(('Concept', relation, 'Concept') for relation in CONCEPT_RELATIONS),
            ('Resource', 'CITES', 'Resource'),
            ('Resource', 'CITES', 'ExternalSource'),
            ('Concept', 'DERIVED_FROM', 'Resource'),
            ('Concept', 'DERIVED_FROM', 'ExternalSource'),
        ],
        additional_node_types=False,
        additional_relationship_types=False,
        additional_patterns=False,
    )
```

`evidence_quote` 是抽取结果进入图的硬门槛。模型只负责返回 quote；字符位置由程序在 `CURRENT_CHUNK` 中重新定位，避免相信 LLM
生成的 offset。

## 调用抽取 SDK

一个 `TextChunk` 对应一个增量抽取窗口。相邻内容只提供消歧上下文，所有可写入证据必须来自 `CURRENT_CHUNK`。

```python
from dataclasses import dataclass

from neo4j_graphrag.experimental.components.entity_relation_extractor import (
    LLMEntityRelationExtractor,
    OnError,
)
from neo4j_graphrag.experimental.components.types import TextChunk, TextChunks
from neo4j_graphrag.llm import OpenAILLM


@dataclass(frozen=True)
class ExtractionWindow:
    resource_id: str
    resource_title: str
    chunk_id: str
    section_path: tuple[str, ...]
    previous_context: str
    current_chunk: str
    next_context: str


def render_window(window: ExtractionWindow) -> str:
    section_path = " / ".join(window.section_path)
    return f"""EXTRACTION_RULES:
- Extract only relations explicitly supported by CURRENT_CHUNK.
- evidence_quote must be an exact continuous substring of CURRENT_CHUNK.
- Use CURRENT_RESOURCE as the source of DEFINES, EXPLAINS and EXAMPLE_OF.
- Return no node or relation when the evidence is insufficient.

CURRENT_RESOURCE:
resource_id: {window.resource_id}
title: {window.resource_title}
section_path: {section_path}

PREVIOUS_CONTEXT:
{window.previous_context}

CURRENT_CHUNK:
{window.current_chunk}

NEXT_CONTEXT:
{window.next_context}
"""


class LearningRelationExtractor:
    def __init__(self, *, model_name: str, api_key: str, base_url: str | None) -> None:
        llm = OpenAILLM(
            model_name=model_name,
            model_params={"temperature": 0},
            api_key=api_key,
            base_url=base_url,
        )
        self._extractor = LLMEntityRelationExtractor(
            llm=llm,
            create_lexical_graph=False,
            on_error=OnError.RAISE,
            max_concurrency=5,
            use_structured_output=True,
        )
        self._schema = build_learning_graph_schema()

    async def extract(self, window: ExtractionWindow):
        chunks = TextChunks(chunks=[
            TextChunk(
                uid=window.chunk_id,
                index=0,
                text=render_window(window),
                metadata={
                    "resource_id": window.resource_id,
                    "chunk_id": window.chunk_id,
                },
            )
        ])
        return await self._extractor.run(
            chunks=chunks,
            schema=self._schema,
            examples=LEARNING_RELATION_EXAMPLES,
        )
```

`use_structured_output=True` 使 SDK 把 `Neo4jGraph` Pydantic 类型作为 `response_format` 交给 LLM，返回内容再由 SDK 执行
`Neo4jGraph.model_validate_json(...)`。`OnError.RAISE` 让失败窗口进入关系抽取任务重试，而不是把抽取失败当成“没有关系”。

`LEARNING_RELATION_EXAMPLES` 至少各覆盖一条：依赖、定义、对比、否定关系、引用，以及一个空结果。示例文本必须是通用学习资料，不使用代码
AST 或特定学科字段。

## 从候选图到可写关系

SDK 返回的 node ID 只是本次抽取内的局部 ID。转换顺序如下：

```text
Neo4jGraph local node ID
  -> evidence_quote 精确定位
  -> Resource endpoint 绑定当前 resource 或解析引用
  -> Concept mention 交给 ConceptResolver
  -> canonical Concept node_id
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
locator 合成为 `SourceRef`。同名 Concept 不在此处直接合并；`ConceptResolver` 用当前 knowledge scope 召回 canonical
candidates，再根据定义和上下文证据绑定。

`neo4j-graphrag` 自带 `SinglePropertyExactMatchResolver`、`FuzzyMatchResolver` 和 `SpaCySemanticMatchResolver`，这些 API
会直接合并 Neo4j 中的实体。这里不调用它们，因为 canonical Concept 必须受 knowledge scope、证据和 resource 增量边界约束；SDK
的职责到候选图结束。

## Neo4j 图写入

所有节点同时带公共标签 `KnowledgeNode`，再带一个类型标签，便于用全局 `node_id` 定位边端点：

```cypher
CREATE CONSTRAINT knowledge_node_id IF NOT EXISTS
FOR (node:KnowledgeNode) REQUIRE node.node_id IS UNIQUE;

CREATE CONSTRAINT resource_node_id IF NOT EXISTS
FOR (node:ResourceNode) REQUIRE node.resource_id IS UNIQUE;
```

进程启动时创建并验证 driver：

```python
from neo4j import AsyncGraphDatabase

driver = AsyncGraphDatabase.driver(
    settings.neo4j_uri,
    auth=(settings.neo4j_user, settings.neo4j_password),
)
await driver.verify_connectivity()
```

Concept 节点和关系批量 upsert：

```python
UPSERT_CONCEPTS = """
UNWIND $concepts AS concept
MERGE (node:KnowledgeNode:ConceptNode {node_id: concept.node_id})
SET node.canonical_key = concept.canonical_key,
    node.label = concept.label
"""

UPSERT_RELATIONS = """
UNWIND $relations AS item
MATCH (source:KnowledgeNode {node_id: item.source_node_id})
MATCH (target:KnowledgeNode {node_id: item.target_node_id})
MERGE (source)-[relation:KNOWLEDGE_RELATION {edge_id: item.edge_id}]->(target)
SET relation.relation_type = item.relation_type,
    relation.origin = item.origin,
    relation.evidence_resource_id = item.evidence_resource_id,
    relation.evidence_ref_ids = item.evidence_ref_ids,
    relation.extractor_version = item.extractor_version,
    relation.qualifiers_json = item.qualifiers_json,
    relation.source_content_revision = item.source_content_revision,
    relation.relation_revision = item.relation_revision
"""


async def write_projection(driver, database, concepts, relations) -> None:
    await driver.execute_query(
        UPSERT_CONCEPTS,
        concepts=concepts,
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

不直接使用 `Neo4jWriter.run(...)`：它写的是 SDK 局部实体图，无法在写前完成 canonical Concept、SourceRef、ACL evidence
Resource、content revision 和 relation revision 的绑定。SDK 输出经过领域转换后统一由 repository 写入。

## 一至两跳图检索

`locate` 先通过 RAG hit 的 `(resource_id, version, chunk_id)` 查 Mongo chunk-to-concept projection，得到 `seed_node_ids`。
`expand` 再从这些 Concept IDs 做固定关系、固定方向的一至两跳展开。

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
个完整 path。`result_builder` 批量读取 `evidence_ref_ids` 对应的 SourceRef，生成 preview 和 `content_ref`，Agent 再决定沿哪个
Concept 继续读。

## 最小验证集

1. schema/structured output：每种关系有正例，端点错误被清理，结果可由 `Neo4jGraph` 校验。
2. evidence：quote 不在 `CURRENT_CHUNK` 时不生成 SourceRef 和边。
3. 跨文档：两个 Resource 的同一 Concept mention 绑定同一 canonical Concept。
4. 增量：修改一个 chunk 只替换该 Resource、该 revision 的证据边。
5. ACL：任一路径边的 evidence Resource 不可读时，整条 path 不返回。
6. 遍历：验证 `REQUIRES` 双向语义、depth 1 和 depth 2 的完整路径。

## 官方资料

- [Neo4j GraphRAG Knowledge Graph Builder](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html)
- [Neo4j GraphRAG API: LLMEntityRelationExtractor](https://neo4j.com/docs/neo4j-graphrag-python/current/api.html#neo4j_graphrag.experimental.components.entity_relation_extractor.LLMEntityRelationExtractor)
- [Neo4j GraphRAG types](https://neo4j.com/docs/neo4j-graphrag-python/current/types.html)
- [Neo4j Python Driver 6.2 async API](https://neo4j.com/docs/api/python-driver/current/async_api.html)
- [Neo4j Cypher EXISTS subqueries](https://neo4j.com/docs/cypher-manual/current/subqueries/existential/)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
