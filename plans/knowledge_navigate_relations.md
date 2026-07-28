# 跨文档实体与关系

## 结论

图谱以通用实体关系为基础，学习与学术关系作为 profile 叠加。不存在专用的 `LearningEntity`：概念只是实体类型之一，人物、组织、产品、技术、方法、数据集、事件、地点和文档都可以成为跨文档导航节点。

```text
Resource / SourceRef
  -> EntityMention
  -> canonical KnowledgeEntity
  -> typed KnowledgeRelation
  -> evidence SourceRef
```

学习场景增加 `DEFINES`、`EXPLAINS`、`REQUIRES` 等关系，论文场景增加 `CITES`、`USES_DATASET`、`USES_METHOD` 等关系，但共享同一套 mention、canonical entity、evidence 和 ACL 契约。

## 社区实践

- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/index/default_dataflow/) 使用通用 `Entity` 和 `Relationship`，实体类型由配置提供，prompt tuning 还可以发现未预先指定的类型。
- [Neo4j GraphRAG KG Builder](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html) 支持手工 schema、自动提取 schema 和 FREE 模式，并可分别控制额外 node type、relationship type 和 pattern。WisePen 使用版本化的宽基础 schema，不按单篇文档动态生成 schema。
- [Graphiti](https://help.getzep.com/graphiti/core-concepts/custom-entity-and-edge-types/) 用 Pydantic 定义领域实体和边，同时保留 `Entity -> Entity: RELATES_TO` fallback，适合“通用基础 + 领域扩展”。
- [Schema.org CreativeWork](https://schema.org/CreativeWork) 提供 `citation`、`mentions`、`about`、`isPartOf`、`isBasedOn` 等文档关系；[W3C PROV-O](https://www.w3.org/TR/prov-o/) 提供 `wasDerivedFrom`、`wasAttributedTo`、`wasRevisionOf` 和 `hadPrimarySource` 等来源关系。
- [Crossref relationship metadata](https://www.crossref.org/documentation/principles-practices/best-practices/relationships/) 和 [Semantic Scholar Academic Graph API](https://api.semanticscholar.org/api-docs/graphs) 都把论文、作者、数据集、引用和引用上下文视为普通研究对象及其关系。

## 图的用途

文档内位置由 RAG hit 的 resource、section ID、heading path、page/offset 和 locator 定位，并由标题树恢复局部语境。图只解决三类跨文档问题：

1. 不同 Resource 是否提到同一个实体。
2. 实体之间存在什么依赖、组成、使用、来源、因果、比较或引用关系。
3. 哪些 Resource 提供该实体的定义、解释、例子和来源证据。

```text
Product A -> DEPENDS_ON -> Technology B
Document A -> CITES -> Document B
Concept A -> REQUIRES -> Concept B
Resource C -> EXPLAINS -> Concept A

Document B -> USES_DATASET -> Dataset D
```

每条边通过 `SourceRef` 指向具体 chunk、section path 和原文 span。

## 通用实体

`KnowledgeEntity` 是 canonical 节点，`entity_type` 只是分类属性。

| entity_type | 典型内容 |
|---|---|
| `CONCEPT` | 术语、原理、主题、问题、指标 |
| `PERSON` | 作者、负责人、历史人物 |
| `ORGANIZATION` | 公司、学校、团队、标准组织 |
| `PRODUCT` | 产品、服务、设备、模型 |
| `TECHNOLOGY` | 技术、协议、框架、语言 |
| `METHOD` | 算法、流程、实验方法 |
| `DATASET` | 数据集、语料、基准 |
| `EVENT` | 发布、实验、事故、会议 |
| `PLACE` | 国家、城市、位置 |
| `DOCUMENT` | 论文、标准、手册、网页、报告 |
| `OTHER` | 证据明确但不属于以上类型的实体 |

实体可带少量 `type_tags` 表达次级角色，例如一个 `PERSON` 同时是 author；角色不产生新的 canonical 节点类型。`Resource` 是当前私有正文的权威投影，不由 LLM 自由创建。`ExternalSource` 表示正文明确引用、但尚未解析为私有 Resource 的外部对象。

## 关系 profiles

### Core

| relation_type | 方向与含义 |
|---|---|
| `MENTIONS` | Resource -> Entity，正文出现该实体 |
| `ABOUT` | Resource/Document -> Entity，正文主题明确围绕该实体 |
| `RELATED_TO` | Entity -> Entity，存在明确但尚未归一到专用类型的关系 |
| `PART_OF` | Entity -> Entity，主体是客体的组成部分 |
| `USES` / `PRODUCES` | Entity -> Entity，主体使用或产生客体 |
| `DEPENDS_ON` | Entity -> Entity，主体依赖客体 |
| `DERIVED_FROM` | Entity/Document -> Entity/Document/Source，主体来源于客体 |
| `IMPLEMENTS` | Product/Technology/Method -> Concept/Method/Standard |
| `APPLIES_TO` | Method/Technology/Concept -> Entity |
| `CAUSES` | Entity/Event -> Entity/Event |
| `COMPARES_WITH` / `CONTRADICTS` | Entity -> Entity，原文明示比较或冲突 |
| `EXTENDS` / `SUPERSEDES` | Entity/Document -> Entity/Document，扩展或替代 |
| `LOCATED_IN` | Entity -> Place |
| `AUTHORED_BY` | Resource/Document -> Person/Organization |

`RELATED_TO` 必须同时保存具体 `predicate`，例如“由……训练”或“与……兼容”。它用于保留 schema 尚未覆盖的高价值显式关系，排序低于专用 relation type。

### Learning

| relation_type | 方向与含义 |
|---|---|
| `DEFINES` | Resource/Document -> Entity，给出正式定义 |
| `EXPLAINS` | Resource/Document -> Entity，解释或推导该实体 |
| `EXAMPLE_OF` | Entity/Resource -> Entity，给出实例 |
| `REQUIRES` | Entity -> Entity，理解或使用主体需要客体 |

### Scholarly

| relation_type | 方向与含义 |
|---|---|
| `CITES` | Resource/Document -> Resource/Document/ExternalSource |
| `PUBLISHED_IN` | Document -> Organization/ExternalSource |
| `USES_DATASET` | Document/Method -> Dataset |
| `USES_METHOD` | Document -> Method |
| `SUPPLEMENTS` | Document/Dataset -> Document |
| `RETRACTS` | Document -> Document |

默认知识库启用 `core + learning + scholarly`。profile 只决定允许的 relation type 和 prompt 示例，不改变底层 DTO、Neo4j 标签或检索 API。

## 抽取流程

```text
changed chunk + bounded context
  -> KnowledgeGraphExtractor
  -> schema validation
  -> evidence validation
  -> EntityResolver
  -> bind canonical KnowledgeEntity IDs
  -> CitationResolver
  -> stage Resource/Entity/Source edges
  -> apply relation revision
```

首次摄入时，每个有效 chunk 都进入一个抽取窗口；没有关系的窗口返回空数组。更新规则见 [indexing](./knowledge_navigate_indexing.md#高频更新)。

关系候选使用 `neo4j-graphrag` 的 `LLMEntityRelationExtractor` 抽取，`GraphSchema` 固定为当前 `graph_schema_version + enabled_profiles` 并启用 structured output。schema 不按单篇文档临时生成，避免同一实体在频繁更新时反复改变类型。SDK 调用、候选图转换和检索核心代码见 [graph implementation](./knowledge_navigate_graph_implementation.md)。

## 输出 DTO

以下 DTO 是 `Neo4jGraph` 经过 evidence 和 endpoint 校验后的领域结果，不是 LLM SDK 的原始输出。

```python
class EntityType(StrEnum):
    CONCEPT = "concept"
    PERSON = "person"
    ORGANIZATION = "organization"
    PRODUCT = "product"
    TECHNOLOGY = "technology"
    METHOD = "method"
    DATASET = "dataset"
    EVENT = "event"
    PLACE = "place"
    DOCUMENT = "document"
    OTHER = "other"


class RelationProfile(StrEnum):
    CORE = "core"
    LEARNING = "learning"
    SCHOLARLY = "scholarly"


class EntityMention(BaseModel):
    mention_id: str
    text: str
    entity_type: EntityType
    evidence_quote: str
    start: int | None
    end: int | None


class RelationEndpoint(BaseModel):
    kind: Literal[
        "entity_mention",
        "current_resource",
        "referenced_resource",
        "external_source",
    ]
    reference: str


class KnowledgeRelationCandidate(BaseModel):
    subject: RelationEndpoint
    object: RelationEndpoint
    relation_type: KnowledgeRelationType
    profile: RelationProfile
    predicate: str | None
    evidence_quote: str
    start: int | None
    end: int | None
    assertion: AssertionKind
    qualifiers: dict[str, JsonScalar]


class KnowledgeRelationBatch(BaseModel):
    mentions: tuple[EntityMention, ...]
    relations: tuple[KnowledgeRelationCandidate, ...]
```

`KnowledgeRelationType` 是三个 profile 关系集合的并集。`predicate` 只在 `RELATED_TO` 时必填；其余关系由枚举本身表达语义。`RelationEndpoint` 使用判别联合，根据 kind 校验 reference。

## Prompt 约束

```text
- 依据当前 chunk 和提供的上下文。
- entity_type 和 relation_type 从当前 schema 选择。
- 每个 entity mention 和 relation 都必须给出连续 evidence_quote。
- RELATED_TO 必须给出具体 predicate，不能只输出“相关”。
- 否定、条件、假设和不确定表达写入 assertion/qualifier。
- 相邻上下文只用于消歧，不能作为可写 evidence。
- 没有明确 evidence 时返回空数组。
```

输入窗口包含 document title、section path、current retrieval leaf、祖先短导语和同 Section 内的有限相邻上下文。相邻上下文只用于消歧，可写 evidence 仍必须来自 current leaf 的 source spans。

## EvidenceValidator

写入关系需要通过以下校验：

1. Pydantic/JSON Schema 合法。
2. mention quote 和 relation evidence 是 current chunk 的连续子串。
3. 程序重新定位并确定 start/end。
4. endpoint kind/reference 组合符合 DTO。
5. entity type、profile、relation type 和 endpoint 组合符合当前 schema。
6. assertion/qualifier 与 evidence 一致。
7. 成功绑定当前 resource/version/chunk/span 的 `SourceRef`。

校验失败的 candidate 记录原因并丢弃；同一窗口的其他 candidate 继续处理。

## 跨文档 EntityResolver

每次解析新增或变化 Resource 的 mention：

```text
mention
  -> normalize text and aliases
  -> stable identifier match
  -> retrieve same-type canonical Entity candidates in current knowledge scope
  -> compare type and local evidence
  -> LLM adjudication only for ambiguous candidates
  -> bind existing Entity or create new Entity
```

规则：

- DOI、URL、ORCID、标准号和产品 ID 等稳定标识优先于名称匹配。
- normalized text 和 embedding 用于召回同类型候选。
- 类型、别名与当前 evidence 决定是否绑定已有 Entity。
- 名称相同但类型或证据冲突时创建独立 Entity。
- 解析结果保留 evidence 和 resolver version。
- 一个 Resource 更新只重算它的 mention、绑定和 incident edges。
- canonical Entity 使用 knowledge scope + entity type + canonical key；最后一个有效 mention 删除后按 grace period 清理孤立节点。

解析结果写入 Mongo chunk-to-entity projection：

```text
resource_id, document_version, chunk_id,
mention_start/end, entity_id, entity_type, extractor_version
```

`locate` 获得 RAG hits 后，按 `(resource_id, version, chunk_id)` 批量读取该 projection。`NodeResolver` 使用 query 与 mention span 选择 KnowledgeEntity nodes；Qdrant payload 无需为关系抽取结果二次改写。

## 引用关系

论文和报告的引用采用“结构解析优先，LLM 补语义”的两阶段处理：

1. 从 reference section、DOI、URL、标题和作者字段构造 `Document/ExternalSource` 候选。
2. 用 DOI、URL 或标题元数据解析已有 Resource；无法解析时保留 ExternalSource。
3. LLM 从正文中的引用上下文抽取 `CITES` evidence，并可在 qualifier 中写 citation intent，例如 background、method、support、contrast。
4. 可选使用 Crossref 或 Semantic Scholar 元数据补全外部标识；外部元数据不替代私有 Resource ACL。

bibliography 中的引用事实和正文中的引用目的分开保存，Agent 可以沿论文、作者、方法和数据集继续阅读。

## Agent 多跳阅读

典型路径：

```text
找产品依赖：
RAG hit -> Product A -> DEPENDS_ON/USES -> Technology B
        -> inbound MENTIONS/EXPLAINS -> other Resources

追论文来源：
RAG hit -> Document A -> CITES -> Document B
        -> USES_METHOD/USES_DATASET -> Method or Dataset

学习前置概念：
RAG hit -> Concept A -> REQUIRES -> Concept B
        -> inbound DEFINES/EXPLAINS -> evidence Resources

查人物与组织：
RAG hit -> Document -> AUTHORED_BY -> Person
        -> RELATED_TO/Organization evidence -> other Resources
```

一次 `expand` 最多两跳。更长路径由 Agent 读取当前 evidence 后选择下一 focus，再次调用 `expand`。
