# 跨文档概念与关系

## 图的用途

文档内位置由 RAG hit 的 resource、section ID、heading path、page/offset 和 locator 定位，并由标题树恢复局部语境。图只解决三类跨文档问题：

1. 不同 Resource 是否在讲同一个概念。
2. 一个概念依赖、来源于、扩展或反驳哪些概念。
3. 哪些 Resource 提供该概念的定义、解释、例子和引用来源。

```text
Concept A
  <- DEFINES  - Resource 1
  <- EXPLAINS - Resource 2
  -> REQUIRES -> Concept B
  <- DEFINES  - Resource 3

Resource 2 -> CITES -> Resource 4 / ExternalSource
```

每条边通过 `SourceRef` 指向具体 chunk、section path 和原文 span。

## 固定关系

Resource 到 Concept：

| 关系           | 含义                   |
|--------------|----------------------|
| `DEFINES`    | Resource 中有该概念的正式定义  |
| `EXPLAINS`   | Resource 对该概念给出解释或推导 |
| `EXAMPLE_OF` | Resource 中有该概念或方法的实例 |

Concept 到 Concept：

| 关系               | 方向                 |
|------------------|--------------------|
| `REQUIRES`       | 主体的理解或使用以前置概念为条件   |
| `DERIVED_FROM`   | 主体明确来源于客体          |
| `EXTENDS`        | 主体在客体基础上增加内容       |
| `APPLIES_TO`     | 方法/概念明确应用于另一个问题或概念 |
| `CONTRASTS_WITH` | 原文明确比较两者           |
| `CONTRADICTS`    | 原文明确指出两者冲突         |
| `SUPERSEDES`     | 主体明确替代客体           |

来源关系：

| 关系             | 端点                                           |
|----------------|----------------------------------------------|
| `CITES`        | Resource -> Resource/ExternalSource          |
| `DERIVED_FROM` | Concept -> Resource/ExternalSource，原文明确给出来源时 |

生产 relation type 固定为上述集合。

## 抽取流程

```text
changed chunk + bounded context
  -> LearningRelationExtractor
  -> schema validation
  -> evidence validation
  -> ConceptResolver
  -> bind canonical Concept IDs
  -> stage Resource/Concept/Source edges
  -> apply relation revision
```

首次摄入时，每个有效 chunk
都进入一个抽取窗口；没有关系的窗口返回空数组。更新规则见 [indexing](./knowledge_navigate_indexing.md#高频更新)。

关系候选使用 `neo4j-graphrag` 的 `LLMEntityRelationExtractor` 抽取，固定
`GraphSchema` 并启用 structured output。SDK 调用、schema、候选图转换和检索核心代码见
[graph implementation](./knowledge_navigate_graph_implementation.md)。

## 输出 DTO

以下 DTO 是 `Neo4jGraph` 经过 evidence 和 endpoint 校验后的领域结果，不是 LLM SDK 的原始输出。

```python
class LearningRelationType(StrEnum):
    DEFINES = "defines"
    EXPLAINS = "explains"
    EXAMPLE_OF = "example_of"
    REQUIRES = "requires"
    DERIVED_FROM = "derived_from"
    EXTENDS = "extends"
    APPLIES_TO = "applies_to"
    CONTRASTS_WITH = "contrasts_with"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    CITES = "cites"


class ConceptMention(BaseModel):
    mention_id: str
    text: str
    evidence_quote: str
    start: int | None
    end: int | None


class RelationEndpoint(BaseModel):
    kind: Literal[
        "concept_mention",
        "current_resource",
        "cited_resource",
        "external_source",
    ]
    reference: str


class LearningRelationCandidate(BaseModel):
    subject: RelationEndpoint
    object: RelationEndpoint
    relation_type: LearningRelationType
    evidence_quote: str
    start: int | None
    end: int | None
    assertion: AssertionKind
    qualifiers: dict[str, JsonScalar]


class LearningRelationBatch(BaseModel):
    mentions: tuple[ConceptMention, ...]
    relations: tuple[LearningRelationCandidate, ...]
```

`RelationEndpoint` 使用判别联合，根据 kind 校验 reference。

## Prompt 约束

```text
- 依据当前 chunk 和提供的上下文。
- relation type 从固定集合选择。
- 每个 concept mention 和 relation 都必须给出连续 evidence_quote。
- 否定、条件、假设和不确定表达写入 assertion/qualifier。
- 没有明确 evidence 时返回空数组。
```

输入窗口包含 document title、section path、current retrieval leaf、祖先短导语和同 Section 内的有限相邻上下文。相邻上下文只用于消歧，可写
evidence 仍必须来自 current leaf 的 source spans。

## EvidenceValidator

写入关系需要通过以下校验：

1. Pydantic/JSON Schema 合法。
2. mention quote 和 relation evidence 是当前窗口的连续子串。
3. 程序重新定位并确定 start/end。
4. endpoint kind/reference 组合符合 DTO。
5. relation type 和 endpoint 组合符合固定 schema。
6. assertion/qualifier 与 evidence 一致。
7. 成功绑定当前 resource/version/chunk/span 的 `SourceRef`。

校验失败的 candidate 记录原因并丢弃；同一窗口的其他 candidate 继续处理。

## 跨文档 ConceptResolver

每次解析新增或变化 Resource 的 mention：

```text
mention
  -> normalize text and aliases
  -> retrieve canonical Concept candidates in current knowledge scope
  -> exact authoritative alias match, if any
  -> compare definition/context evidence
  -> LLM adjudication only for ambiguous candidates
  -> bind existing Concept or create new Concept
```

规则：

- normalized text 和 embedding 用于召回候选。
- 定义与上下文 evidence 决定是否绑定已有 Concept。
- 冲突或证据不足时创建独立 Concept。
- 解析结果保留 evidence 和 resolver version。
- 一个 Resource 更新只重算它的 mention、绑定和 incident edges。
- canonical Concept 使用 knowledge scope + canonical key；最后一个有效 mention 删除后按 grace period 清理孤立节点。

解析结果写入 Mongo chunk-to-concept projection：

```text
resource_id, document_version, chunk_id,
mention_start/end, concept_id, extractor_version
```

`locate` 获得 RAG hits 后，按 `(resource_id, version, chunk_id)` 批量读取该 projection。`NodeResolver` 使用 query 与
mention span 选择 Concept nodes；ES/Qdrant payload 无需为关系抽取结果二次改写。

## Agent 多跳阅读

典型路径：

```text
查 A 的前置知识：
RAG hit -> Concept A -> REQUIRES -> Concept B
        -> inbound DEFINES/EXPLAINS -> other Resources -> SourceRef

查 A 的不同解释：
Concept A -> inbound DEFINES/EXPLAINS
          -> Resource 1 / Resource 2 / Resource 3

追来源：
Concept A -> DERIVED_FROM -> Resource/ExternalSource
Resource -> CITES -> Resource/ExternalSource

找冲突资料：
Concept A -> CONTRADICTS/CONTRASTS_WITH -> Concept B
          -> 两侧 evidence Resources
```

一次 `expand` 最多两跳。更长路径由 Agent 读取当前 evidence 后选择下一 focus，再次调用 `expand`。
