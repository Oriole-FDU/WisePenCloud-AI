# WisePen RAG Service V3 重构方案

本文定义 RAG V3 的业务能力、领域模型、索引流程、查询流程和实施边界。V3 是全量重写，旧数据库直接重建，不设计旧数据迁移、双写、兼容层或版本回滚。

Common 不是 RAG 的契约来源。RAG 只复用 Common 中不带 RAG 业务含义的文档结构能力：`DocumentChunker`、`DocumentChunk`、`Section`、`Page`、`Anchor`、`OutlineNode` 和 `SourceSpan`。资源、权限、元数据、检索、图谱和服务响应全部由 RAG 自己定义。

## 一、先明确 V3 要交付什么

完整旧方案第四章定义的是四个 **RAG 能力域**，第五章才讨论上层可能怎样调用。V3 的实现以第四章为准：先把每个能力域中的应用服务、子能力和数据边界做完整，再由 HTTP 暴露这些方法。四个能力域不代表只能有四个端点，更不代表必须与四个 MCP 工具一一对应。

```text
                     Chat / MCP / 其他上层调用方
                                │
                     按任务组合一个或多个方法
                                │
                                ▼
┌────────────────────────────────────────────────────────────┐
│                    RAG Application Services                │
│                                                            │
│  1. 混合检索核          2. 知识图谱检索核                  │
│  召回、精排、动态装箱    图种子、证据回查、证据精排          │
│                                                            │
│  3. 文档结构与标题拓展   4. 原文确定性阅读                  │
│  命中投影、邻域、全局大纲  Section、Page、Evidence、Span     │
└────────────────────────────────────────────────────────────┘
```

四项能力的边界如下：


| 能力               | 解决的问题                                       | 返回内容                                                                           |
| -------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------ |
| 混合检索核         | 不知道答案在哪一篇、哪一节时找局部事实           | Dense/BM25 并集、统一精排、动态装箱，以及命中 Section 的投影森林                   |
| 知识图谱检索核     | 查概念关系、依赖、演化、引用或跨文档联系         | Low/High/Hybrid 图种子，以及按 Query 重新排序后的 Top-K 证据正文和受证据支撑的子图 |
| 文档结构与标题拓展 | 已知资源或命中位置，希望理解文档空间             | 命中投影森林、目标 Section 十字邻域、受层级限制的全局大纲                          |
| 原文确定性阅读     | 已经知道逻辑或物理坐标，希望跳过模糊检索直接阅读 | 按 Section、Page、图谱证据或 SourceSpan 从权威 Markdown 切出的原文                 |

V3 不保存静态父块。混合检索和图谱检索都以小 `DocChunk` 或图谱事实命中，返回前再依据 Section、相邻 Chunk、Evidence 和原文坐标动态装箱。上层可以把若干 RAG 方法包装成一个工具，也可以在一个工作流中连续调用多个方法；这种编排不能反向改变 RAG 的领域模型。

## 二、文档进入 RAG 后怎样流转

```text
document-ready 事件
        │
        ▼
读取权威资源、ACL 和垂类 metadata
        │
        ▼
调用 Common DocumentChunker 一次
        │
        ├── Section / Page / Anchor / Outline
        └── DocumentChunk[]
                    │
                    ▼
投影为 RAG Document + DocChunk[]
        │
        ├── contextualize 与 key terms
        ├── Dense / BM25 正文索引
        └── LLM / 确定性图谱抽取
                    │
                    ▼
写 Mongo、Qdrant、Neo4j 并校验
                    │
                    ▼
最后原子替换当前 Document
```

`Document` 是当前已发布资源的聚合根。新版本的所有索引写完并通过校验后才替换它。构建失败只清理本次未发布数据，不提供“回滚到上一状态”的业务动作，也不保存 `deleted` 标记。

## 三、领域模型

### 3.1 唯一内容版本 `ContentRevision`

V3 只保留内容版本。Markdown 内容没有变化，Section、Chunk 和图谱证据的坐标基础就没有变化；实现代码、模型或索引配置变化需要重建时执行全量重建，不在每条业务数据上再发明 `structure_revision`、`retrieval_revision`、`graph_revision` 或 `profile_id`。

```python
class ContentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    document_version: int = Field(ge=1)
    content_sha256: str

    @classmethod
    def create(
        cls,
        *,
        resource_id: str,
        document_version: int,
        raw_content: str,
    ) -> "ContentRevision":
        return cls(
            resource_id=resource_id,
            document_version=document_version,
            content_sha256=hashlib.sha256(
                raw_content.encode("utf-8")
            ).hexdigest(),
        )

    @property
    def revision_id(self) -> str:
        return (
            f"{self.resource_id}@{self.document_version}#"
            f"{self.content_sha256[:16]}"
        )
```

Kafka offset 是消息系统的消费状态，不是资源领域事实，不进入 `Document` 或任何资源状态模型。是否处理旧事件以权威资源当前 `document_version` 和已发布 `Document.revision.document_version` 判断。

### 3.2 文档结构

Common 负责解析，RAG 保存其结果并用于查询。RAG 不复制 Common 的结构模型，也不为 Common 没有定义的行为增加 `structure_failed` 一类伪状态。

```python
class StructureMode(StrEnum):
    SECTIONED = "sectioned"
    FLAT_TEXT = "flat_text"
    EMPTY = "empty"


class DocumentStructure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: StructureMode
    total_length: int = Field(ge=0)
    sections: tuple[Section, ...]
    pages: tuple[Page, ...]
    anchors: tuple[Anchor, ...]
    outline: tuple[OutlineNode, ...]
```

调用 `DocumentChunker.chunk()` 后：有 Section 就是 `sectioned`；无 Section 但存在正文就是 `flat_text`；没有正文就是 `empty`。调用异常直接让本次构建失败，不把异常伪装成某种结构类型。

### 3.3 强类型垂类 metadata

旧方案中 Document/Chunk metadata 的设计意图是正确的：垂类数据不能塞进一个无约束的 `dict`，也不能等需要图谱时再用 `field_path` 猜字段含义。metadata 是垂类建模的入口，同时服务于过滤、图谱确定性解析和结果展示。

```python
class BaseDocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: str


class GeneralDocumentMetadata(BaseDocumentMetadata):
    doc_type: Literal["general"] = "general"


class BaseChunkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: str


class GeneralChunkMetadata(BaseChunkMetadata):
    doc_type: Literal["general"] = "general"
```

垂类插件定义自己的 Pydantic 模型。下面的论文模型只是扩展方式示例，不是 V3 首期必须实现的完整论文 Ontology：

```python
class PaperAuthor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    author_id: str | None = None
    name: str
    institutions: tuple[str, ...] = ()


class PaperReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    doi: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None


class PaperDocumentMetadata(BaseDocumentMetadata):
    metadata_type: Literal["paper"] = "paper"
    title: str
    doi: str | None = None
    arxiv_id: str | None = None
    authors: tuple[PaperAuthor, ...] = ()
    venue: str | None = None
    year: int | None = None
    references: tuple[PaperReference, ...] = ()


class PaperChunkMetadata(BaseChunkMetadata):
    metadata_type: Literal["paper"] = "paper"
    section_type: Literal[
        "abstract",
        "introduction",
        "related_work",
        "method",
        "experiment",
        "conclusion",
        "references",
        "other",
    ]
```

插件负责解析和消费自己的模型，RAG 核心不需要认识 `doi`、`authors` 或 `references`：

```python
class VerticalRagPlugin(Protocol):
    metadata_type: str
    document_metadata_model: type[BaseDocumentMetadata]
    chunk_metadata_model: type[BaseChunkMetadata]
    ontology: "OntologySchema"

    def project_chunk_metadata(
        self,
        *,
        document: "Document",
        chunk: DocumentChunk,
    ) -> BaseChunkMetadata: ...

    def qdrant_filter_payload(
        self,
        metadata: BaseDocumentMetadata | BaseChunkMetadata,
    ) -> dict[str, str | int | float | bool | list[str]]: ...

    def graph_fact_producers(self) -> Sequence["GraphFactProducer"]: ...
```

插件注册表根据事件中的 `metadata_type` 选择模型。没有注册的类型直接拒绝入库，不能退化为 `metadata: dict[str, Any]`。

### 3.4 `Document` 聚合根

`Document` 统一持有当前权威内容、结构、权限和文档级 metadata。Chunk 不复制 ACL，查询通过 `resource_id` 回到 Document 判权。

```python
class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    revision: ContentRevision
    raw_content: str
    structure: DocumentStructure
    acl: ResourceAcl
    metadata: SerializeAsAny[BaseDocumentMetadata]
```

Mongo 中一个 `resource_id` 只有一个已发布 `Document`。构建中的新版本存放在构建临时集合或带本次 `content_revision` 的明细记录中；成功后才替换当前 Document，之后异步删除旧 revision 的 Chunk 和索引。

### 3.5 `DocChunk`

`DocChunk` 是 Common `DocumentChunk` 的 RAG 业务化投影。它是小粒度检索、图谱证据挂载和动态装箱的共同基础，不保存静态父块。

```python
class DocChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int = Field(ge=0)
    section_id: str | None
    section_path: tuple[str, ...]
    raw_text: str
    source_spans: tuple[SourceSpan, ...]
    page_labels: tuple[str, ...]
    anchor_labels: tuple[str, ...]

    contextual_prefix: str = ""
    key_terms: tuple[str, ...] = ()
    metadata: SerializeAsAny[BaseChunkMetadata]

    def get_semantic_text(self) -> str:
        parts = (
            " > ".join(self.section_path),
            self.contextual_prefix.strip(),
            self.raw_text,
        )
        return "\n\n".join(part for part in parts if part)

    def get_lexical_text(self) -> str:
        terms = " ".join(
            dict.fromkeys(term.strip() for term in self.key_terms if term.strip())
        )
        parts = (
            " > ".join(self.section_path),
            terms,
            self.raw_text,
        )
        return "\n".join(part for part in parts if part)
```

`contextual_prefix` 和 `key_terms` 是 DocChunk 自己的增强字段。Dense/BM25 adapter 只调用两个无参数方法，不保存 `semantic_index_text`、`lexical_index_text`，也不再引入 Manifest 或专门投影模型。

### 3.6 ACL

ACL 的模型和判断顺序直接参考旧服务，因为这一部分已有明确消费者和完整测试。模型仍归 RAG 所有，不从 Common 导出。

```python
@dataclass(slots=True)
class PermissionScope:
    user_id: str
    group_roles: dict[str, GroupRoleType | None] = field(default_factory=dict)

    @property
    def managed_group_ids(self) -> set[str]:
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role in (GroupRoleType.OWNER, GroupRoleType.ADMIN)
        }

    @property
    def joined_group_ids(self) -> set[str]:
        return {
            group_id
            for group_id, role in self.group_roles.items()
            if role is not None and role is not GroupRoleType.NOT_MEMBER
        }


@dataclass(slots=True)
class GroupResourceAcl:
    group_id: str
    default_readable: bool
    readable_users: list[str] = field(default_factory=list)
    excluded_read_users: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResourceAcl:
    resource_id: str
    acl_revision: int
    owner_id: str
    readable_users: list[str] = field(default_factory=list)
    excluded_read_users: list[str] = field(default_factory=list)
    group_acls: list[GroupResourceAcl] = field(default_factory=list)

    def can_read(self, scope: PermissionScope) -> bool:
        if scope.user_id == self.owner_id:
            return True
        if scope.user_id in self.readable_users:
            return True
        if scope.user_id in self.excluded_read_users:
            return False
        for group_acl in self.group_acls:
            if group_acl.group_id in scope.managed_group_ids:
                return True
            if group_acl.group_id not in scope.joined_group_ids:
                continue
            if group_acl.default_readable:
                if scope.user_id not in group_acl.excluded_read_users:
                    return True
            elif scope.user_id in group_acl.readable_users:
                return True
        return False
```

参考：

- `services/wisepen-rag-service/src/rag/domain/models/acl.py`
- `services/wisepen-rag-service/src/rag/application/rag/acl/authorizer.py`
- `services/wisepen-rag-service/src/rag/application/rag/acl/refresher.py`
- `services/wisepen-rag-service/tests/rag/test_acl.py`

### 3.7 图谱事实模型

图节点、图边和文本 Chunk 保持正交。节点/边描述“图中有什么”，Evidence 描述“为什么可以返回这个事实”。V3 首期不做贡献聚合、mention count、边权重或置信度演化。

```python
class GraphNodeKind(StrEnum):
    ENTITY = "entity"
    RESOURCE = "resource"
    EXTERNAL_RESOURCE = "external_resource"


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_kind: GraphNodeKind
    category: str
    name: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str
    source_node_id: str
    relation_type: str
    target_node_id: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class BaseGraphEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    evidence_type: str
    resource_id: str
    content_revision: str


class ChunkGraphEvidence(BaseGraphEvidence):
    evidence_type: Literal["chunk"] = "chunk"
    chunk_ids: tuple[str, ...]
    quote_text: str
    source_span: SourceSpan
```

普通 LLM 抽取只产生 `ChunkGraphEvidence`。它必须能从当前 Document 的 `raw_content[source_span]` 精确读回 `quote_text`。

垂类 metadata 如果能确定性产生图事实，但事实没有对应 Markdown 文本，证据模型也由垂类插件强类型定义。例如论文引用可以定义为：

```python
class PaperCitationEvidence(BaseGraphEvidence):
    evidence_type: Literal["paper_citation"] = "paper_citation"
    citing_title: str
    cited_reference: PaperReference


class PaperDocumentEvidence(BaseGraphEvidence):
    evidence_type: Literal["paper_document"] = "paper_document"
    title: str
    doi: str | None = None
    arxiv_id: str | None = None
```

这与 `field_path + value_sha256` 的通用审计壳不同：字段含义由论文模型直接表达，论文插件也知道如何把它渲染成可精排的证据文本。RAG 核心只要求每种 Evidence 有对应 resolver：

```python
class GraphEvidenceResolver(Protocol):
    evidence_type: str

    async def resolve(
        self,
        evidence: BaseGraphEvidence,
    ) -> "GraphEvidenceBlock": ...
```

### 3.8 Ontology 与垂类图谱插件

Ontology 是垂类图谱的语义模型，不是一个 prompt 字符串。它定义实体类型、关系类型、合法端点以及给抽取器的说明。LLM 生产器和确定性生产器都必须经过同一个 Ontology 校验。

```python
class EntitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    description: str
    node_kind: GraphNodeKind = GraphNodeKind.ENTITY


class RelationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_type: str
    description: str
    source_categories: frozenset[str]
    target_categories: frozenset[str]


class OntologySchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ontology_id: str
    description: str
    entities: dict[str, EntitySpec]
    relations: dict[str, RelationSpec]

    def validate_entity(self, category: str) -> EntitySpec:
        try:
            return self.entities[category]
        except KeyError as exc:
            raise ValueError(f"unknown entity category: {category}") from exc

    def validate_relation(
        self,
        *,
        source_category: str,
        relation_type: str,
        target_category: str,
    ) -> RelationSpec:
        try:
            relation = self.relations[relation_type]
        except KeyError as exc:
            raise ValueError(f"unknown relation: {relation_type}") from exc
        if source_category not in relation.source_categories:
            raise ValueError(f"invalid source for relation: {relation_type}")
        if target_category not in relation.target_categories:
            raise ValueError(f"invalid target for relation: {relation_type}")
        return relation

    def extraction_instructions(self) -> str:
        entity_lines = [
            f"- {name}: {spec.description}"
            for name, spec in self.entities.items()
        ]
        relation_lines = [
            f"- {name}: {spec.description}; "
            f"source={sorted(spec.source_categories)}; "
            f"target={sorted(spec.target_categories)}"
            for name, spec in self.relations.items()
        ]
        return "\n".join(
            ["Entities:", *entity_lines, "Relations:", *relation_lines]
        )
```

论文扩展可以声明论文、作者、机构以及引用关系。这里只给一个完整但有限的示例，说明 metadata、Ontology 和确定性解析如何衔接：

```python
PAPER_ONTOLOGY_EXAMPLE = OntologySchema(
    ontology_id="paper",
    description="论文、作者、机构与论文引用关系",
    entities={
        "paper": EntitySpec(
            category="paper",
            description="有标题并可选 DOI/arXiv ID 的论文",
            node_kind=GraphNodeKind.EXTERNAL_RESOURCE,
        ),
        "author": EntitySpec(
            category="author",
            description="论文作者",
        ),
        "institution": EntitySpec(
            category="institution",
            description="作者所属机构",
        ),
    },
    relations={
        "CITES": RelationSpec(
            relation_type="CITES",
            description="一篇论文引用另一篇论文",
            source_categories=frozenset({"paper"}),
            target_categories=frozenset({"paper"}),
        ),
        "AUTHORED_BY": RelationSpec(
            relation_type="AUTHORED_BY",
            description="论文由作者创作",
            source_categories=frozenset({"paper"}),
            target_categories=frozenset({"author"}),
        ),
        "AFFILIATED_WITH": RelationSpec(
            relation_type="AFFILIATED_WITH",
            description="作者隶属于机构",
            source_categories=frozenset({"author"}),
            target_categories=frozenset({"institution"}),
        ),
    },
)
```

这个示例不意味着 V3 首期要开发完整论文知识库。它证明扩展点不是空壳：论文 metadata 有强类型字段，论文 Ontology 有对应语义，确定性 producer 可以直接读取 `PaperDocumentMetadata.references` 生成 `CITES` 和 `PaperCitationEvidence`。

## 四、正文索引

### 4.1 Common 的使用边界

RAG 调用 `DocumentChunker().chunk(raw_content)`，一次获得结构和 Chunk。具体 Markdown 解析、块识别、超长块拆分和 Section 归属由 Common 管理，重构方案不重复解释 Common 内部规则。

RAG adapter 只做四件事：

1. 把 Common 结构保存进 `DocumentStructure`。
2. 把 `DocumentChunk` 投影为 `DocChunk`。
3. 调用垂类插件生成 Document/Chunk metadata。
4. 校验每个 `SourceSpan` 能从 `raw_content` 回读。

### 4.2 contextualize

旧服务的 contextualize 已经有可复用的 prompt、并发控制、缓存和客户端关闭逻辑。V3 保留行为，把结果直接写入对应 DocChunk 的 `contextual_prefix` 和 `key_terms`。

参考：

- `services/wisepen-rag-service/src/rag/application/rag/index/contextualize.py`
- `services/wisepen-rag-service/src/rag/utils/llm_clients/query.py`
- `services/wisepen-rag-service/src/rag/utils/llm_clients/embedding.py`

### 4.3 Dense/BM25 并集池化

Dense 使用 `DocChunk.get_semantic_text()`，BM25 使用 `DocChunk.get_lexical_text()`。两路独立取 Top-K，按 `chunk_id` 求并集，最后统一交给 Cross-Encoder。

```python
@dataclass(frozen=True, slots=True)
class RoutedChunkHit:
    chunk_id: str
    route: Literal["dense", "bm25"]
    route_rank: int
    route_score: float


def union_pool(
    dense_hits: Sequence[RoutedChunkHit],
    lexical_hits: Sequence[RoutedChunkHit],
    *,
    limit: int,
) -> list[RoutedChunkHit]:
    result: list[RoutedChunkHit] = []
    seen: set[str] = set()
    for rank in range(max(len(dense_hits), len(lexical_hits))):
        for route_hits in (dense_hits, lexical_hits):
            if rank >= len(route_hits):
                continue
            hit = route_hits[rank]
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            result.append(hit)
            if len(result) == limit:
                return result
    return result
```

并集阶段不把 Dense 和 BM25 的异构分数相加。Cross-Encoder 是唯一的跨路统一评分器。

### 4.4 动态装箱

精排命中仍是小 Chunk，返回给调用方前按以下顺序动态生成连续阅读窗口：

1. 短 Section：直接读取该 Section 的 `subtree_span`，但受单窗口字符上限约束。
2. 长 Section：以命中 Chunk 为中心向同一 Section 的相邻 Chunk 扩展。
3. Section 过短且仍有预算：补充线性相邻 Section，但不跨资源。
4. 多个命中窗口重叠或相邻时合并。
5. 按最佳命中分排序并取 Top-N。

所有正文最后都从当前 `Document.raw_content` 按连续 `SourceSpan` 重建。装箱结果没有稳定 ID，不写 Mongo，不作为下一次检索的输入。

参考旧实现：

- `services/wisepen-rag-service/src/rag/application/rag/index/constructor/reading_blocks.py`
- `services/wisepen-chat-service/src/chat/application/tools/session_tools/cached_tool_output_tools/search_by_semantics.py`

## 五、图谱构建

### 5.1 双轨事实生产

图谱构建保留旧方案真正有价值的“双轨”思想，但不保留置信度聚合：

```text
                     Document + DocChunk[]
                              │
          ┌───────────────────┴───────────────────┐
          ▼                                       ▼
LLM 语义 producer                         垂类确定性 producer
Instructor + Pydantic + OpenAI             typed metadata / AST / 引用表
ChunkGraphEvidence                         垂类 typed evidence
          └───────────────────┬───────────────────┘
                              ▼
                    GraphExtractionBatch[]
                              ▼
             Ontology、端点、证据与身份校验
                              ▼
             GraphNode / GraphEdge / Evidence
```

```python
class GraphNodeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str
    identity_key: str | None = None
    node_kind: GraphNodeKind | None = None
    category: str
    name: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    evidence: SerializeAsAny[BaseGraphEvidence]


class GraphEdgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_local_id: str
    relation_type: str
    target_local_id: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    assertion: Literal["affirmed", "negated", "conditional", "uncertain"]
    evidence: SerializeAsAny[BaseGraphEvidence]


class GraphExtractionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    producer_id: str
    nodes: tuple[GraphNodeCandidate, ...]
    edges: tuple[GraphEdgeCandidate, ...]


class GraphFactProducer(Protocol):
    producer_id: str

    async def produce(
        self,
        *,
        document: Document,
        chunks: Sequence[DocChunk],
        ontology: OntologySchema,
    ) -> GraphExtractionBatch: ...
```

`identity_key` 由确定性 producer 在拥有 DOI、ORCID、资源 ID 等权威标识时填写，例如 `doi:10.1000/example`。LLM producer 默认不填写，后台按 `ontology_id + category + normalized_name` 生成兜底 ID。LLM 不直接生成全局 node ID 或 edge ID。

### 5.2 LLM 语义抽取技术栈

LLM 图谱抽取只使用 Instructor、Pydantic 和 OpenAI 官方 SDK，不使用 `neo4j-graphrag` 的 extractor、candidate graph 或 SDK 类型。

```python
class ExtractedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str
    category: str
    name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    evidence_quote: str
    evidence_start: int


class ExtractedEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_local_id: str
    relation_type: str
    target_local_id: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    assertion: Literal["affirmed", "negated", "conditional", "uncertain"]
    evidence_quote: str
    evidence_start: int


class GraphExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)


class OpenAIGraphFactProducer:
    producer_id = "openai_graph_extraction"

    def __init__(self, *, client: AsyncOpenAI, model: str) -> None:
        self._client = instructor.from_openai(client)
        self._model = model

    async def extract_window(
        self,
        *,
        window_text: str,
        ontology: OntologySchema,
    ) -> GraphExtractionResponse:
        return await self._client.chat.completions.create(
            model=self._model,
            response_model=GraphExtractionResponse,
            messages=[
                {
                    "role": "system",
                    "content": (
                        GRAPH_EXTRACTION_PROMPT
                        + "\n\n"
                        + ontology.extraction_instructions()
                    ),
                },
                {"role": "user", "content": window_text},
            ],
        )
```

Instructor 负责结构化响应与有限重试。后台仍必须逐条验证 Ontology、端点、assertion 和 quote。整体响应无法解析才重试；单条候选不合法只丢弃该条并记录原因。

### 5.3 窗口和证据校验

抽取窗口由连续 DocChunk 组成，证据只能来自主窗口。相邻窗口可以重叠 Chunk，但不能把只存在于补充上下文中的文字当成证据。

```python
class GraphExtractionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    content_revision: str
    chunk_ids: tuple[str, ...]
    source_span: SourceSpan
    text: str


def locate_quote(
    window: GraphExtractionWindow,
    *,
    quote: str,
    relative_start: int,
) -> SourceSpan | None:
    if relative_start < 0:
        return None
    if window.text[relative_start : relative_start + len(quote)] != quote:
        return None
    return SourceSpan(
        start_offset=window.source_span.start_offset + relative_start,
        end_offset=window.source_span.start_offset + relative_start + len(quote),
    )
```

以下旧代码行为直接迁移：窗口源坐标映射、`local_id` 端点解析、非 affirmed 关系丢弃、重叠窗口证据去重、稳定 ID 生成和候选顺序无关的合并。

参考：

- `services/wisepen-rag-service/src/rag/application/rag/index/graph/windows.py`
- `services/wisepen-rag-service/src/rag/application/rag/index/graph/candidate_validator.py`
- `services/wisepen-rag-service/src/rag/application/rag/index/graph/candidate_merge.py`
- `services/wisepen-rag-service/tests/rag/test_graph_extraction.py`
- `services/wisepen-rag-service/tests/rag/test_graph_merge.py`

### 5.4 确定性垂类 producer 示例

论文插件直接读取强类型 metadata，不通过通用字段路径：

```python
class PaperCitationProducer:
    producer_id = "paper_citations"

    async def produce(
        self,
        *,
        document: Document,
        chunks: Sequence[DocChunk],
        ontology: OntologySchema,
    ) -> GraphExtractionBatch:
        metadata = cast(PaperDocumentMetadata, document.metadata)
        source_local_id = "source-paper"
        source_evidence = PaperDocumentEvidence(
            evidence_id=f"paper:{document.resource_id}:self",
            resource_id=document.resource_id,
            content_revision=document.revision.revision_id,
            title=metadata.title,
            doi=metadata.doi,
            arxiv_id=metadata.arxiv_id,
        )
        nodes: list[GraphNodeCandidate] = [
            GraphNodeCandidate(
                local_id=source_local_id,
                identity_key=f"resource:{document.resource_id}",
                node_kind=GraphNodeKind.RESOURCE,
                category="paper",
                name=metadata.title,
                aliases=tuple(
                    value
                    for value in (metadata.doi, metadata.arxiv_id)
                    if value is not None
                ),
                evidence=source_evidence,
            )
        ]
        edges: list[GraphEdgeCandidate] = []

        for index, reference in enumerate(metadata.references):
            target_local_id = f"reference:{index}"
            evidence = PaperCitationEvidence(
                evidence_id=f"paper:{document.resource_id}:reference:{index}",
                resource_id=document.resource_id,
                content_revision=document.revision.revision_id,
                citing_title=metadata.title,
                cited_reference=reference,
            )
            nodes.append(
                GraphNodeCandidate(
                    local_id=target_local_id,
                    identity_key=(f"doi:{reference.doi}" if reference.doi else None),
                    node_kind=GraphNodeKind.EXTERNAL_RESOURCE,
                    category="paper",
                    name=reference.title,
                    aliases=((reference.doi,) if reference.doi else ()),
                    evidence=evidence,
                )
            )
            edges.append(
                GraphEdgeCandidate(
                    source_local_id=source_local_id,
                    relation_type="CITES",
                    target_local_id=target_local_id,
                    assertion="affirmed",
                    evidence=evidence,
                )
            )

        return GraphExtractionBatch(
            producer_id=self.producer_id,
            nodes=tuple(nodes),
            edges=tuple(edges),
        )
```

示例只证明扩展链路完整：typed metadata -> deterministic producer -> Ontology 校验 -> typed evidence。作者、机构、领域概念等完整论文能力以后在论文插件中继续扩展，不修改 RAG 核心图模型。

## 六、由服务核心能力推导出的应用契约

本章落实完整旧方案第四章。四个能力域是 RAG 内部的稳定业务边界；请求模型按真正不同的操作拆开，避免为了迁就某个工具而塞入一个巨大的 `mode + 一堆可空字段` 请求。第五章中的 MCP 只可能调用这些方法，不能决定这些方法怎样实现。

### 6.1 公共返回模型

检索与阅读结果都必须带回可核验坐标。正文来自当前 `Document.raw_content`，不是 Qdrant payload 或图数据库属性。

```python
class ReadingWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: tuple[str, ...]
    text: str
    source_span: SourceSpan
    matched_chunk_ids: tuple[str, ...]
    page_labels: tuple[str, ...]
    score: float


class SectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str
    title: str
    level: int
    section_path: tuple[str, ...]
    own_length: int
    subtree_length: int
    child_count: int


class RelativeSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int = Field(gt=0)


class TextFragment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    content_revision: str
    text: str
    source_spans: tuple[SourceSpan, ...]
    section_path: tuple[str, ...] = ()
    page_labels: tuple[str, ...] = ()
    highlights: tuple[RelativeSpan, ...] = ()
```

`ReadingWindow` 是检索命中经过动态装箱后的连续阅读窗口。`TextFragment` 是确定性读取结果；Common 的 `Section.content_spans` 可能包含多个直属正文区间，因此这里使用 `source_spans`。

### 6.2 能力域一：混合检索核

混合检索核负责 ACL/metadata 前置过滤、Dense 与 BM25 双路召回、并集池化、统一精排、动态装箱，以及把最终命中的 Section 投影为最小活性森林。这些步骤共同构成一个完整检索能力，不由 MCP 拼装。

```python
Scalar = str | int | float | bool


class MetadataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata_type: str
    values: dict[str, Scalar | list[Scalar]]


class HybridRetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_query: str = Field(min_length=1)
    lexical_query: str | None = None
    resource_ids: tuple[str, ...] | None = None
    metadata_filter: MetadataFilter | None = None
    candidate_k: int = Field(default=20, ge=1, le=100)
    top_k: int = Field(default=5, ge=1, le=20)


class ChunkHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    dense_score: float | None = None
    lexical_score: float | None = None
    rerank_score: float


class HybridRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hits: tuple[ChunkHit, ...]
    windows: tuple[ReadingWindow, ...]
    hit_projection: tuple["HitProjectionTree", ...]
```

`lexical_query` 为空时使用 `semantic_query`。如果上层为了 BM25 准确性单独生成了关键词查询，则显式传入 `lexical_query`。`MetadataFilter` 必须先由 `metadata_type` 对应的垂类插件校验，再由基础设施适配器编译成 Qdrant filter；不能直接把任意字典下推到数据库。

应用服务的职责直接写成：

```python
class HybridRetrievalService:
    async def search(
        self,
        query: HybridRetrievalQuery,
        permission_scope: PermissionScope,
    ) -> HybridRetrievalResult:
        qdrant_filter = self.filter_compiler.compile(
            permission_scope=permission_scope,
            resource_ids=query.resource_ids,
            metadata_filter=query.metadata_filter,
        )
        dense_hits, lexical_hits = await asyncio.gather(
            self.chunk_index.search_dense(
                query.semantic_query, qdrant_filter, query.candidate_k
            ),
            self.chunk_index.search_lexical(
                query.lexical_query or query.semantic_query,
                qdrant_filter,
                query.candidate_k,
            ),
        )
        candidates = union_by_chunk_id(dense_hits, lexical_hits)

        # Qdrant 只是候选索引；精排前必须回查当前 Chunk、Document 和 ACL。
        chunks = await self.chunk_repository.get_current_readable(
            [item.chunk_id for item in candidates], permission_scope
        )
        ranked = await self.reranker.rank(
            query=query.semantic_query,
            documents=[chunk.raw_text for chunk in chunks],
        )
        top_hits = build_chunk_hits(chunks, candidates, ranked, query.top_k)
        windows = await self.parent_context_assembler.assemble(top_hits)
        projection = await self.structure_service.project_hits(
            HitProjectionQuery.from_hits(top_hits), permission_scope
        )
        return HybridRetrievalResult(
            hits=tuple(top_hits),
            windows=tuple(windows),
            hit_projection=projection.trees,
        )
```

这里返回 `hits` 是为了审计“哪些小块进入了装箱”，返回 `windows` 是为了阅读，返回 `hit_projection` 是为了显示命中在文档中的位置。三者各有真实消费者，不能互相替代。

### 6.3 能力域二：知识图谱检索核

Low、High、Hybrid 是图谱检索核内部真实存在的检索策略：Low 搜节点，High 搜关系主题，Hybrid 并行执行两路并取并集。它们来自第四章的图检索语义，不是 MCP 工具模式。

V3 对第四章的算法做一项已确认修正：不做 PPR，不按 `mention_count`、边权重或贡献聚合结果决定最终排序。图检索先找到节点/边，再把它们绑定的证据块拉回，最终按 Query 对证据正文统一 rerank 并取 Top-K。

```python
class GraphRetrievalStrategy(StrEnum):
    LOW = "low"
    HIGH = "high"
    HYBRID = "hybrid"


class GraphRetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1)
    strategy: GraphRetrievalStrategy = GraphRetrievalStrategy.HYBRID
    resource_ids: tuple[str, ...] | None = None
    seed_k: int = Field(default=20, ge=1, le=100)
    top_k: int = Field(default=5, ge=1, le=20)
    include_one_hop: bool = True


class GraphEvidenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    evidence_ids: tuple[str, ...]
    supported_target_ids: tuple[str, ...]
    resource_id: str
    content_revision: str
    text: str
    chunk_ids: tuple[str, ...]
    source_span: SourceSpan
    rerank_score: float


class GraphSubgraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class GraphRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: GraphRetrievalStrategy
    subgraph: GraphSubgraph
    evidence_blocks: tuple[GraphEvidenceBlock, ...]
    windows: tuple[ReadingWindow, ...]
```

三个策略只改变“从哪里找图种子”，后半段完全共用：

```python
class GraphRetrievalService:
    async def search(
        self,
        query: GraphRetrievalQuery,
        permission_scope: PermissionScope,
    ) -> GraphRetrievalResult:
        if query.strategy is GraphRetrievalStrategy.LOW:
            graph_hits = await self._search_nodes(query, permission_scope)
        elif query.strategy is GraphRetrievalStrategy.HIGH:
            graph_hits = await self._search_edges(query, permission_scope)
        else:
            node_hits, edge_hits = await asyncio.gather(
                self._search_nodes(query, permission_scope),
                self._search_edges(query, permission_scope),
            )
            graph_hits = union_graph_hits(node_hits, edge_hits)

        if query.include_one_hop:
            graph_hits = await self.graph_store.attach_one_hop(graph_hits)

        evidence_blocks = await self._rank_evidence(
            query=query.query,
            graph_hits=graph_hits,
            permission_scope=permission_scope,
            top_k=query.top_k,
        )
        windows = await self.parent_context_assembler.assemble_evidence(
            evidence_blocks
        )
        return GraphRetrievalResult(
            strategy=query.strategy,
            subgraph=keep_evidence_supported_subgraph(
                graph_hits, evidence_blocks
            ),
            evidence_blocks=tuple(evidence_blocks),
            windows=tuple(windows),
        )

    async def _rank_evidence(
        self,
        *,
        query: str,
        graph_hits: Sequence[GraphNode | GraphEdge],
        permission_scope: PermissionScope,
        top_k: int,
    ) -> list[GraphEvidenceBlock]:
        evidence_ids = list(dict.fromkeys(
            evidence_id
            for hit in graph_hits
            for evidence_id in hit.evidence_ids
        ))
        evidences = await self.evidence_store.get_current_readable(
            evidence_ids, permission_scope
        )
        blocks = await self.evidence_resolver.resolve_many(evidences)
        unique_blocks = deduplicate_evidence_blocks(blocks)
        ranked = await self.reranker.rank(
            query=query,
            documents=[block.text for block in unique_blocks],
        )
        return apply_evidence_ranking(unique_blocks, ranked, top_k)
```

`attach_one_hop` 只是确定性读取邻接节点和边，不执行扩散打分。最终子图必须由 Top-K 证据反向裁剪：没有进入 Top-K 证据块支撑的图元不随结果返回。

这一流程参考 LightRAG 的“entity/relation -> related chunks -> merge -> rerank -> Top-K”主线，而不照搬其数据模型。核对版本为 HKUDS/LightRAG commit `09f8249fdd998585fc976b86f7b2e2d5523d9db9`，重点参考 `operate.py::_perform_kg_search`、`_merge_all_chunks`、`_find_related_text_unit_from_entities`、`_find_related_text_unit_from_relations` 与 `utils.py::process_chunks_unified`。

### 6.4 能力域三：文档结构与标题拓展

这一能力域必须保留第四章的三个独立方法，不能缩成“outline 或 neighborhood”两个模式，更不能把命中投影森林当成混合检索响应里的一个随意字段而不定义算法。

```python
class SectionHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    section_id: str
    hit_count: int = Field(ge=1)
    best_score: float


class HitProjectionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hits: tuple[SectionHit, ...]

    @classmethod
    def from_hits(cls, hits: Sequence[ChunkHit]) -> "HitProjectionQuery":
        return aggregate_section_hits(hits)


class ProjectionSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["section"] = "section"
    section: SectionSummary
    context_path: tuple[str, ...] = ()
    hit_count: int = Field(default=0, ge=0)
    is_stub: bool = False
    has_unhit_children: bool = False
    children: tuple["ProjectionItem", ...] = ()


class ProjectionGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["omitted_gap"] = "omitted_gap"
    omitted_count: int = Field(ge=1)


ProjectionItem = Annotated[
    ProjectionSection | ProjectionGap,
    Field(discriminator="kind"),
]


class HitProjectionTree(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    root: ProjectionSection


class HitProjectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trees: tuple[HitProjectionTree, ...]


class NeighborhoodQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    section_id: str
    sibling_steps: int = Field(default=1, ge=0, le=5)


class SectionNeighborhood(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current: SectionSummary
    ancestors: tuple[SectionSummary, ...]
    siblings: tuple[SectionSummary, ...]
    children: tuple[SectionSummary, ...]
    previous_linear: SectionSummary | None
    next_linear: SectionSummary | None


class GlobalOutlineQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    max_level: int = Field(default=2, ge=1, le=6)


class OutlineSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: SectionSummary
    children: tuple["OutlineSection", ...] = ()


class GlobalOutlineResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    roots: tuple[OutlineSection, ...]


class DocumentStructureService(Protocol):
    async def project_hits(
        self,
        query: HitProjectionQuery,
        permission_scope: PermissionScope,
    ) -> HitProjectionResult: ...

    async def get_neighborhood(
        self,
        query: NeighborhoodQuery,
        permission_scope: PermissionScope,
    ) -> SectionNeighborhood: ...

    async def get_global_outline(
        self,
        query: GlobalOutlineQuery,
        permission_scope: PermissionScope,
    ) -> GlobalOutlineResult: ...
```

`project_hits()` 必须落实以下规则，规则名称用于测试，不要求代码照搬名称：

调用前先忽略 `section_id=None` 的 flat-text 命中，再按 `resource_id` 分组构树；因此一次跨资源检索可以返回多棵、分属不同文档的投影树。

1. 跨大章且共同祖先退化为文档根时拆成多棵树，不用空洞目录强行连接。
2. 没有分叉价值的祖先单链折叠进 `context_path`。
3. 祖先层未命中的兄弟节点不展开。
4. 命中层只暴露最左命中前一个、最右命中后一个兄弟作为 `is_stub=True` 的路标。
5. 同级命中之间跨度大于两个节点时插入 `ProjectionGap`。
6. Stub 不递归展开子节点，只通过 `has_unhit_children` 告知仍可下钻。

`get_neighborhood()` 返回祖先链、当前节点、指定步数内的兄弟、直属子节点，以及线性顺序上的前后节点。`get_global_outline()` 只返回 `level <= max_level` 的骨架，并通过 `subtree_length` 告诉上层每个节点大致有多少正文。三者都只读 `Document.structure`，不访问 Qdrant，不调用 LLM。

### 6.5 能力域四：原文确定性阅读

完整旧方案第四章定义了 Section、图谱证据和绝对 SourceSpan 三条确定性读取通道。现有代码中已经实现的 Page label 读取同样是稳定的物理坐标读取能力，因此一并保留。四个方法共享底层切片器，但输入模型分开，调用方不需要构造互斥字段组合。

```python
class SectionReadScope(StrEnum):
    DIRECT = "direct"
    SUBTREE = "subtree"


class ReadSectionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    section_id: str
    scope: SectionReadScope = SectionReadScope.DIRECT


class ReadPagesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    page_labels: tuple[str, ...] = Field(min_length=1, max_length=20)


class GraphTargetType(StrEnum):
    NODE = "node"
    EDGE = "edge"


class ReadGraphEvidenceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    target_type: GraphTargetType
    target_id: str
    context_chars: int = Field(default=200, ge=0, le=2000)


class ReadSpansQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    content_revision: str
    source_spans: tuple[SourceSpan, ...] = Field(min_length=1, max_length=20)


class DeterministicReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fragments: tuple[TextFragment, ...]


class DeterministicReadingService(Protocol):
    async def read_section(
        self,
        query: ReadSectionQuery,
        permission_scope: PermissionScope,
    ) -> DeterministicReadResult: ...

    async def read_pages(
        self,
        query: ReadPagesQuery,
        permission_scope: PermissionScope,
    ) -> DeterministicReadResult: ...

    async def read_graph_evidence(
        self,
        query: ReadGraphEvidenceQuery,
        permission_scope: PermissionScope,
    ) -> DeterministicReadResult: ...

    async def read_spans(
        self,
        query: ReadSpansQuery,
        permission_scope: PermissionScope,
    ) -> DeterministicReadResult: ...
```

四个方法的确定行为如下：


| 方法                  | 坐标来源                                          | 读取行为                                                                                 |
| ----------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `read_section`        | `Section.content_spans` 或 `Section.subtree_span` | `DIRECT` 拼接直属正文区间并排除子节，`SUBTREE` 连续读取整个子树                          |
| `read_pages`          | `Page.source_span`                                | 按 Common 已解析出的字符串页标签批量读取，不把页标签强转成整数                           |
| `read_graph_evidence` | 当前图元关联的`GraphEvidence`                     | 读取证据原始区间并向前后扩展`context_chars`，`highlights` 标出证据在返回文本中的相对位置 |
| `read_spans`          | 调用方传回的绝对坐标                              | 先核对`content_revision` 与范围边界，再原样执行 Python 字符串切片                        |

所有方法都先校验 ACL，再从当前 `Document.raw_content` 切片。Section 和 Page 读取只依赖 Mongo；图谱证据读取先从 Mongo Evidence 找坐标，但绝不把 Evidence 中的 quote 当权威正文；Span 读取要求携带 `content_revision`，防止旧坐标误切新内容。

### 6.6 应用服务与传输层的映射

HTTP 应按应用服务方法暴露清晰的内部端点。下面是建议映射，路径名可以按项目路由规范调整，但不能合并成 MCP 风格的万能请求：


| RAG 应用方法                                      | 建议内部端点                     |
| --------------------------------------------------- | ---------------------------------- |
| `HybridRetrievalService.search`                   | `POST /retrieval/search`         |
| `GraphRetrievalService.search`                    | `POST /graph/search`             |
| `DocumentStructureService.project_hits`           | `POST /structure/hit-projection` |
| `DocumentStructureService.get_neighborhood`       | `POST /structure/neighborhood`   |
| `DocumentStructureService.get_global_outline`     | `POST /structure/outline`        |
| `DeterministicReadingService.read_section`        | `POST /content/sections`         |
| `DeterministicReadingService.read_pages`          | `POST /content/pages`            |
| `DeterministicReadingService.read_graph_evidence` | `POST /content/graph-evidence`   |
| `DeterministicReadingService.read_spans`          | `POST /content/spans`            |

当前旧代码的 `locateCandidate`、`getSurroundingOutline`、`readSections`、`readPages` 和 `expandGraph` 证明了“RAG 暴露可组合原语、上层另做工具编排”这条边界是可行的。V3 不要求兼容这些旧路径，但应参考其中已经正确的鉴权、错误映射和应用服务拆分方式。

上层调用示例只用于说明，不进入 RAG 契约：一个“搜索”工具可以直接调用 `HybridRetrievalService.search`；一个“导航”工具可以根据用户意图选择 `get_neighborhood` 或 `get_global_outline`；一个“阅读”工具可以选择四种确定性读取方法；复杂工作流也可以先图谱检索，再用 `read_graph_evidence` 复核某条关系。工具数量、工具参数简化、模型可见字段和上下文预算都由 Chat/MCP 决定。

## 七、存储边界

### 7.1 Mongo

Mongo 是以下内容的权威来源：

- 当前已发布 `Document`；
- 当前及构建中的 `DocChunk`；
- `GraphNode`、`GraphEdge` 和各类强类型 `GraphEvidence`；
- contextualize 后写回 DocChunk 的增强字段；
- 本地 ACL 投影。

Mongo 不保存静态父块、`ResourceIndexState`、Kafka offset、逻辑删除标记、contribution 或边权重。

### 7.2 Qdrant

Qdrant 使用三个逻辑 collection：

1. `document_chunks`：Dense/BM25 的 DocChunk 检索点。
2. `graph_nodes`：节点名称、别名、类别和描述的向量检索点。
3. `graph_edges`：关系类型、keywords 和描述的向量检索点。

payload 保存候选过滤真正需要的 `resource_id`、`content_revision`、业务 ID、ACL 投影和垂类插件声明的标量过滤字段。正文、图事实和 ACL 真值仍要回 Mongo 核验。

### 7.3 Neo4j

Neo4j 保存已校验的节点、边和 `evidence_ids`，负责按节点/关系读取拓扑和可选 1-hop 邻接。它不承担：

- LLM 抽取；
- Ontology 校验；
- PPR；
- 置信度聚合；
- 最终证据排序；
- 权威正文保存。

### 7.4 发布、更新和删除

新版本发布：

```text
读取权威资源与 document_version
  -> 构建新 revision 的 Chunk 和图谱数据
  -> 写 Mongo/Qdrant/Neo4j
  -> 回读校验数量、坐标、ACL 和 evidence
  -> 原子替换当前 Document
  -> 异步物理删除旧 revision 数据
```

如果构建失败，本次新 revision 不发布并清理其部分数据，当前 Document 不变。这不是业务回滚：系统没有历史版本列表、rollback API、`deleted` 状态或恢复动作。

删除事件执行物理删除：Mongo Document/Chunk/Evidence、Qdrant points、Neo4j 资源与关联图数据、生成缓存全部删除。重复删除应当幂等。删除失败进入重试/DLQ，但不写逻辑删除记录。

旧事件判断只比较权威 `document_version`，不持久化 Kafka offset：

```python
def should_process(
    incoming_version: int,
    published_document: Document | None,
) -> bool:
    if published_document is None:
        return True
    return incoming_version > published_document.revision.document_version
```

ACL 事件仍只携带 `resourceId`。处理器重新读取权威 ACL，写 Mongo 后同步 Qdrant 和 Neo4j；相同 ACL revision 可以执行后端补偿，旧 revision 不覆盖新值。

## 八、旧代码哪些直接迁移，哪些只参考


| 能力                        | 处理方式                                                   | 参考位置                                                    |
| ----------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------- |
| ACL`can_read` 与真值表      | 直接迁移行为和测试                                         | `rag/domain/models/acl.py`、`tests/rag/test_acl.py`         |
| 权威 ACL action-mask 投影   | 迁移字段映射和单调 revision 规则                           | `core/persistence/mongo/authoritative_acl_reader.py`        |
| ACL 同步和后端补偿          | 迁移编排顺序，删除逻辑删除状态                             | `application/rag/acl/refresher.py`                          |
| QueryClient/EmbeddingClient | 迁移客户端生命周期、异常和关闭逻辑                         | `rag/utils/llm_clients/`                                    |
| contextualize/key terms     | 迁移 prompt、并发和缓存行为，结果写回 DocChunk             | `application/rag/index/contextualize.py`                    |
| 动态装箱                    | 迁移 Section 边界、相邻扩展和回源原则                      | `index/constructor/reading_blocks.py`、Chat semantic search |
| Section/Page 确定性读取     | 直接迁移 ACL、坐标回读和批量读取行为                       | `application/rag/read/content.py`、`api/endpoints/read.py`  |
| Section 十字邻域            | 迁移祖先、兄弟和直属子节读取行为，再补线性前后节点         | `application/rag/read/`、`api/endpoints/read.py`            |
| RAG 与上层工具边界          | 迁移“RAG 暴露原语、上层自行编排”的分层方式，不保留旧 URL | `api/endpoints/locate.py`、`read.py`、`expand.py`           |
| 图谱窗口                    | 迁移连续窗口与原文坐标映射                                 | `index/graph/windows.py`                                    |
| 图谱候选校验                | 迁移 quote、端点、assertion 逐条校验                       | `index/graph/candidate_validator.py`                        |
| 图谱稳定合并                | 迁移 local ID、稳定 ID 和证据去重                          | `index/graph/candidate_merge.py`                            |
| 图谱 LLM SDK                | 删除旧实现，改用 Instructor + Pydantic + OpenAI            | 旧`graph/llm.py`、`graph/extractor.py`                      |
| 图谱查询                    | 保留“图命中回到证据正文”，改成证据块统一 rerank          | `navigate/graph_expander.py` 与 LightRAG 官方实现           |

完整旧方案中保留的设计意图：Document 聚合根、强类型 metadata、图文正交、三态节点、Ontology、双轨事实生产，以及第四章定义的四个能力域。四个能力域内部必须保留混合检索五步流程、图谱 Low/High/Hybrid 种子检索、标题拓展三方法、确定性阅读三通道；Page label 读取作为现有代码中已验证的第四种确定性读取方法一并迁移。

旧方案中明确删除的设计：PPR、mention count 排序、边权重和置信度增量聚合。当前重构过程中误加且明确删除的设计：多级 revision、`profile_id`、`ResourceIndexState`、Kafka offset、`deleted`、Manifest、Chunk set ID、静态父块、结构化字段路径证据和 contribution 聚合。

## 九、实施顺序

### 阶段 1：Document、Common adapter 和 ACL

实现 `ContentRevision`、强类型 metadata 注册、`Document`、`DocChunk`、Common adapter、Mongo repository 和 ACL。随后实现 `read_section`、`read_pages`、`read_spans` 三个只依赖 Document 的确定性读取方法。完成后可以正确入库、做权限判断，并从权威 Markdown 按逻辑或物理坐标回读。

### 阶段 2：混合检索和动态装箱

迁移 QueryClient/EmbeddingClient/contextualize，实现 DocChunk 两个索引文本方法、Dense/BM25、Union Pooling、Cross-Encoder 和动态装箱。同时完成 `project_hits`、`get_neighborhood`、`get_global_outline`，让混合检索能够返回有明确算法定义的命中投影森林，而不是临时拼一个树字段。

### 阶段 3：图谱构建与检索

实现 Ontology、Instructor + Pydantic + OpenAI producer、确定性 producer 协议、窗口与证据校验、Mongo/Qdrant/Neo4j adapter，以及 Low/High/Hybrid 三种图种子检索和“图命中 -> Evidence -> 统一 rerank -> Top-K -> 动态装箱”。补齐 `read_graph_evidence`，使任意返回图元都能穿透到权威正文。论文只实现契约级示例测试，不把完整论文 Ontology 纳入首期交付。

### 阶段 4：事件和运行质量

实现 document-ready、ACL、物理删除、重试/DLQ、health/readiness、metrics、日志和故障注入。没有旧库迁移、兼容读、双写、逻辑删除或回滚开发项。

## 十、验收标准

### 10.1 Document 与 Common

- 只有 `ContentRevision`，不存在 structure/retrieval/graph revision 或 `profile_id`。
- `Document` 明确持有 raw content、结构、ACL 和强类型 metadata。
- `DocChunk` 从 Common DocumentChunk 投影，拥有 `contextual_prefix`、`key_terms` 和无参数索引文本方法。
- `sectioned`、`flat_text`、`empty` 与 Common 实际行为一致；异常不伪装成结构类型。
- 所有文本 `SourceSpan` 都能从当前 Document 原文精确回读。

### 10.2 垂类扩展

- 未注册 metadata type 不能以 dict 形式混入系统。
- 垂类插件同时声明 Document metadata、Chunk metadata、Ontology、过滤投影和确定性 graph producer。
- 论文示例能够从 `PaperDocumentMetadata.references` 生成 `CITES` 与 `PaperCitationEvidence`，核心模块不读取论文专用字段。

### 10.3 四个能力域

- 混合检索执行 ACL/metadata 前置过滤、Dense/BM25 并集、Mongo 当前版本回查、统一 rerank 和动态装箱；结果同时包含可审计的 Chunk hits、阅读窗口和命中投影森林。
- 图谱 Low 策略从节点找种子，High 策略从关系主题找种子，Hybrid 对两路做并集；三者共用 Evidence 回查、统一 rerank、Top-K 和动态装箱，不执行 PPR、贡献聚合或边权重排序。
- 标题拓展分别提供 `project_hits`、`get_neighborhood`、`get_global_outline`；投影森林的分树、单链折叠、兄弟桩和 gap 折叠规则都有独立单元测试。
- 确定性阅读分别提供 `read_section`、`read_pages`、`read_graph_evidence`、`read_spans`；其中 Section 支持 direct/subtree，图证据返回相对高亮，Span 读取拒绝过期 revision 和越界坐标。
- 所有公开应用方法都先校验 ACL；所有由索引或图数据库得到的候选在返回前再次核验当前 Document、ContentRevision 和原文坐标。
- HTTP 集成测试按应用服务方法验证，不以 MCP 工具数量、工具名称或工具请求 schema 作为 RAG 验收标准。

### 10.4 图谱

- LLM 抽取只使用 Instructor + Pydantic + OpenAI 官方 SDK。
- Neo4j 不导入 `neo4j-graphrag` SDK 类型，也不承担抽取和排序。
- 非 affirmed、Ontology 不合法、端点缺失或 quote 无法回读的候选不能入图。
- 每个公开节点和边至少有一个当前用户可读的 Evidence。
- 被返回的子图只保留被 Top-K 证据块支撑的节点和边。

### 10.5 事件与删除

- 领域模型中不存在 Kafka offset、`deleted` 或可回滚状态。
- 更新只在新版本完整写入并校验后替换当前 Document。
- 删除执行物理清理，重复删除幂等，失败通过重试/DLQ 处理。
- 旧事件以权威 `document_version` 判断，不依赖消息 offset 进入业务数据。

## 十一、完成定义

V3 完成必须同时满足：

1. 第四章四个能力域的每个子能力都有明确请求、响应、服务流程和集成测试；不能只做四个同名端点就宣称完成。
2. Document、DocChunk、metadata、Ontology、Evidence 和存储字段前后一致。
3. Common 只提供结构工具，RAG 业务契约不由 Common 反向定义。
4. 混合检索和图谱检索都以小 Chunk 命中、统一精排、动态连续正文交付。
5. 图谱语义抽取使用 Instructor + Pydantic + OpenAI，垂类确定性解析通过插件接入。
6. ACL、工具 LLM 客户端、contextualize、图谱窗口和证据校验等旧代码优点已迁移并有回归测试。
7. 被明确否决的多级 revision、profile、状态表、逻辑删除、PPR 和贡献聚合没有以其他名称重新出现。
8. MCP 或其他上层工具只组合 RAG 应用方法，没有把工具的 `mode`、互斥可空字段或响应包装下沉为 RAG 领域契约。
