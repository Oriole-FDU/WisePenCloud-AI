# RAG V4 子方案 09：VectorStorage 与 GraphStorage 最小存储合同

## 0. 设计修正

上一版把“存储接口隔离”误解成了“所有业务模型都必须拆成完全无业务含义的原子记录”。本方案改为吸收 LlamaIndex 的整体做法：

- 使用少量贯穿 ingestion、projection、retrieval 的框架级模型；
- 允许 Chunk、Entity、Relationship 以统一投影形态进入向量存储；
- GraphStorage 直接承载 RAG 已经确定的 GraphNode/GraphEdge 事实模型；
- 读写接口隔离只约束依赖方向，不制造额外的 Repository、Record、Router 层；
- 具体后端由一个绑定 collection/graph 的 Adapter 实现。

## 1. 已确认边界

1. VectorStorage 和 GraphStorage 都使用 Reader/Writer Protocol 做依赖隔离；
2. 同一个 Qdrant 或 Neo4j Adapter 可以同时实现 Reader 和 Writer；
3. Collection 绑定 Storage 实例，不在每条记录上携带 namespace；
4. 向量存储使用统一的 `VectorRecord`、`VectorQuery`、`VectorMatch`，以承载 Chunk、Entity、Relationship 的异构向量投影；
5. GraphStorage 直接使用统一的 RAG 图节点和关系模型，不再新增一套 `GraphNodeRecord`/`GraphRelationRecord`；
6. Storage 使用通用 `MetadataFilters`；resource、content revision 和 plugin projection metadata 都只是过滤字段；
7. ACL 计算、active revision 选择、ontology 解释和 local/global/hybrid 编排不属于 Storage；
8. Storage 提供批量 upsert/query/delete，revision replacement 由对应 Indexer 编排；
9. GraphStorage 提供节点、关系、邻接和有界遍历原语，不提供 local/global/hybrid；
10. Mongo Chunk facts（含图谱 metadata）作为可重建输入，Neo4j GraphStorage 作为拓扑及来源 Chunk 关联投影；
11. 不增加额外 Repository 层、Storage Router 或跨存储一致性机制。

内容事实采用同样的接口隔离：`ContentReader`、`ContentWriter` 组合成 `ContentStorage`。统一后端分别存取 Document、Chunk，不把两者统一成 Node；模型位于 `core/schema/content.py`。

## 2. LlamaIndex 对本方案的启发

### 2.1 VectorStore

LlamaIndex 的 `VectorStore` 以统一 Node 类型承载不同索引能力，核心接口集中在：

```text
add(nodes)
delete(...)
query(VectorStoreQuery)
```

它没有为每一种业务对象创建独立的 VectorStore 协议。RAG 采用同样的思路：Chunk、Entity、Relationship 在进入向量库前都转换成统一 `VectorRecord`，但它们的业务语义仍由上层 projection writer 和 collection 配置决定。

### 2.2 PropertyGraphStore

LlamaIndex 的 PropertyGraphStore 直接使用统一的 labelled node、relation 和 triplet，并提供：

```text
get nodes
get triplets
get relation map
upsert nodes
upsert relations
delete
```

RAG 保留同样的形态。GraphNode/GraphEdge 的图谱 metadata 和来源 Chunk 关联保存在可重建的 GraphStorage 投影中，原始抽取事实保存在 Chunk facts；不增加 Mongo GraphFactStore，也不把 local/global/hybrid 下沉到 GraphStorage。

### 2.3 StorageContext

不同 collection 通过不同 Storage 实例组合，而不是在每条记录中携带 namespace：

```python
@dataclass(frozen=True, slots=True)
class RagStorageContext:
    chunk_vectors: VectorStorage
    entity_vectors: VectorStorage
    relation_vectors: VectorStorage
    graph: GraphStorage
```

这只是依赖组合对象，不是动态 Router，也不改变各 Storage 的最小接口。

### 2.4 ContentStorage

内容存储已经独立实现在 `storage/content.py`：Reader 按明确 revision 批量读取 Document、按 ID 批量读取 Chunk，并按 revision 顺序读取 Chunk；Writer 批量 upsert 两种对象，按 revision 或资源物理删除二者。MongoContentStorage 绑定 `document_revisions`、`doc_chunks` 两个集合，显式创建身份唯一索引，每批 500 条有序 upsert。

内容写入不推断 active、不隐式删除批次之外的 Chunk、不解释 ACL；删除由调用方先撤销可见性。ChunkFactsIndexer 只依赖 ContentWriter。向量与图适配器继续只保存可重建投影。

## 3. VectorStorage

### 3.1 Reader/Writer

```python
class VectorReader(Protocol):
    async def query(self, request: VectorQuery) -> list[VectorMatch]: ...


class VectorWriter(Protocol):
    async def upsert(self, records: Sequence[VectorRecord]) -> WriteReceipt: ...

    async def delete(self, filters: MetadataFilters) -> DeleteReceipt: ...


class VectorStorage(VectorReader, VectorWriter, Protocol):
    """绑定一个 collection/index 的向量适配器。"""
```

Reader/Writer 是类型层面的依赖隔离；`QdrantVectorStorage` 仍然是一个具体 Adapter，同时实现两者。

### 3.2 统一向量投影模型

```python
@dataclass(frozen=True, slots=True)
class VectorRecord:
    record_id: str
    text: str
    embedding: Sequence[float]
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorQuery:
    embedding: Sequence[float] | None = None
    text: str | None = None
    top_k: int = 10
    filters: MetadataFilters | None = None


@dataclass(frozen=True, slots=True)
class VectorMatch:
    record_id: str
    score: float
    metadata: Mapping[str, object] = field(default_factory=dict)
```

`VectorRecord` 是为了让不同业务对象共享一套向量写入和查询接口，并不意味着 Chunk、Entity、Relationship 在领域层被合并。Projection Writer 负责将它们各自的语义文本和 identity 投影为该类型：

```text
Chunk.retrieval_text       -> chunk_vectors
GraphNode semantic text    -> entity_vectors
GraphEdge semantic text    -> relation_vectors
```

VectorStorage 不负责生成 semantic text、决定 collection 或解释 metadata。

### 3.3 Collection 绑定

```python
chunk_vectors = QdrantVectorStorage(client, collection="rag_chunks")
entity_vectors = QdrantVectorStorage(client, collection="rag_entities")
relation_vectors = QdrantVectorStorage(client, collection="rag_relations")
```

collection 是 Adapter 实例配置：

- 不在 VectorRecord 增加 namespace 字段；
- 不建立运行时 Storage Router；
- 同一 Qdrant Adapter 类型可以绑定多个 collection；
- 不同 collection 的字段约束由各自 Projection Writer 保证。

## 4. GraphStorage

### 4.1 直接使用 RAG 图模型

GraphStorage 直接接收已由 ontology 校验过的 RAG 图投影：

```python
class GraphReader(Protocol):
    async def get_nodes(
        self,
        *,
        node_ids: Sequence[str] = (),
        filters: MetadataFilters | None = None,
    ) -> list[GraphNode]: ...

    async def get_relations(
        self,
        *,
        edge_ids: Sequence[str] = (),
        filters: MetadataFilters | None = None,
    ) -> list[GraphEdge]: ...

    async def get_rel_map(
        self,
        request: GraphTraversalQuery,
    ) -> list[GraphPath]: ...


class GraphWriter(Protocol):
    async def upsert_nodes(
        self,
        nodes: Sequence[GraphNode],
    ) -> WriteReceipt: ...

    async def upsert_relations(
        self,
        edges: Sequence[GraphEdge],
    ) -> WriteReceipt: ...

    async def delete(self, filters: MetadataFilters) -> DeleteReceipt: ...


class GraphStorage(GraphReader, GraphWriter, Protocol):
    """绑定一个图后端的拓扑存储适配器。"""
```

不再增加只为适配 Storage 而存在的 `GraphNodeRecord`、`GraphRelationRecord`。GraphNode/GraphEdge 已经是统一图事实模型，Storage 只是负责保存和读取它们。

### 4.2 GraphStorage 原语

GraphStorage 必须提供：

- 按 node/edge ID 读取；
- 按通用 MetadataFilters 读取；
- 批量 upsert nodes/edges；
- 按条件批量删除；
- 一跳邻接或关系映射读取；
- 带 direction、max_depth、relation type 和 limit 的有界遍历。

GraphStorage 不提供：

- `local`、`global`、`hybrid`；
- query keyword 到实体/关系的向量召回；
- ACL、active revision 或 PermissionScope 解释；
- ontology 规则解释；
- rerank、Candidate/Fusion 或响应组装；
- 强制 Cypher 接口。

Neo4j 可以用 Cypher 实现这些原语，但 Cypher 只是 Adapter 内部细节。

## 5. MetadataFilters 与 Plugin

### 5.1 通用过滤模型

Storage 接收通用 `MetadataFilters`，参考 LlamaIndex 的 `MetadataFilter(key, value, operator)` 和 `MetadataFilters(filters, condition)`：

- 单项过滤表达字段、值和比较操作；
- 多项过滤支持 `AND/OR/NOT` 组合；
- Adapter 将其翻译为 Qdrant payload 或 Neo4j property predicate；
- Adapter 对不支持的操作明确报错；
- Storage 不理解 plugin、ontology、ACL 和 revision 选择。

### 5.2 Plugin 继续编译垂类过滤

当前 plugin 已经有两项相关能力：

1. `filter_values(document)`：将 Document metadata 投影为索引字段；
2. `DeclarativeMetadataFilter` + `compile_filter()`：将垂类请求模型编译为底层条件。

新合同只改变它们的输出目标：

```python
class RagPlugin:
    def build_projection_metadata(
        self,
        document: Document,
    ) -> Mapping[str, object]: ...

    def compile_filters(
        self,
        request_filter: BaseModel | None,
    ) -> MetadataFilters: ...
```

plugin 仍然负责：

- 哪些 Document metadata 可以进入 projection metadata；
- 哪些请求字段对外开放；
- 请求字段对应哪个 projection metadata key；
- 请求值和类型校验。

Storage 只执行编译结果。现有 `FilterOp` 可以继续作为 plugin 内部声明 DSL，但 `MetadataFilterCondition` 不再作为 Qdrant/Neo4j 的共同公共类型。

### 5.3 ACL 与 revision

上层在调用 Storage 前完成用户身份、ACL 和 active revision 决策，再将 resource/revision 作为普通 `MetadataFilters` 传入：

```text
trusted context
  -> ACL capability
  -> selected revision
  -> MetadataFilters(resource_id, content_revision, plugin fields)
  -> Storage
```

Storage 不自行读取 active pointer，也不把 PermissionScope 传给 Qdrant 或 Neo4j Adapter。

## 6. Chunk 图事实与图投影

```text
Mongo Chunk facts
  = Chunk content + metadata["entities"] + metadata["relationships"]

GraphIndexer
  -> GraphStorage (GraphNode/GraphEdge + source Chunk associations)
  -> entity VectorStorage
  -> relation VectorStorage
```

- 持久化 Chunk metadata 是 GraphExtractor 输出的重建输入，来源 Chunk identity 隐含表达事实来源；
- GraphStorage 是可重建的 Neo4j 拓扑及来源 Chunk 关联投影；图查询从其读取来源 Chunk IDs，再由 Mongo Chunk reader 批量回查正文；
- Entity/Relationship 向量是可重建 projection；
- ChunkFactsIndexer 保存最终 Chunk facts；GraphIndexer/VectorIndexer 负责各自投影的批量 upsert/delete 和完成性检查；
- 投影重建从 Chunk metadata 重新执行确定性归一和 ontology 校验，不重新调用 LLM；
- Storage 不提供 `replace_revision`；
- 不引入 Mongo、Neo4j、Qdrant 之间的事务或统一观察点。

## 7. 实施顺序

### 09-A：统一 MetadataFilters

- 以通用 `MetadataFilters` 替换 repository 专用条件类型；
- 保留 plugin 的 projection metadata builder 和声明式 filter compiler；
- Qdrant/Neo4j Adapter 各自实现过滤翻译。

### 09-B：VectorStorage

- 定义 Reader/Writer Protocol 和统一 VectorRecord/Query/Match；
- 实现 collection-bound Qdrant Adapter；
- Chunk、Entity、Relationship 分别由 VectorIndexer/GraphIndexer 投影到对应 Storage 实例。

### 09-C：GraphStorage

- 定义 Reader/Writer Protocol；
- 直接使用 GraphNode、GraphEdge 和 traversal query/path 模型；
- 实现 Neo4j topology Adapter；
- 将 local/global/hybrid 留给 GraphRetriever。

### 09-D：Indexer 投影迁移

- VectorIndexer 使用 VectorWriter 批量 upsert/delete；
- GraphIndexer 使用 GraphWriter 批量 upsert/delete；
- ChunkFactsIndexer 保存 Transform 后的 Chunk facts；
- 不新增 Repository、Router 或跨库一致性层。

## 8. 验收证据

1. 同一 Qdrant/Neo4j Adapter 可以按 Reader 或 Writer 接口注入；
2. Chunk、Entity、Relationship 可以通过统一 VectorRecord 进入不同 collection；
3. GraphStorage 直接承载 GraphNode/GraphEdge，不存在额外 graph record 层；
4. Collection 由 Storage 实例绑定，不依赖 Record namespace；
5. Storage 只执行通用 MetadataFilters，不解释 ACL、revision 或 plugin；
6. plugin 能把垂类 metadata 和请求 filter 编译成通用过滤模型；
7. VectorStorage 支持批量 upsert/query/delete；
8. GraphStorage 支持节点、关系、邻接和有界遍历；
9. GraphStorage 不包含 local/global/hybrid；
10. revision replacement 留在对应 Indexer；
11. GraphStorage 和 VectorStorage 可仅从 Mongo Chunk facts 重建或替换；
12. 不存在额外 Repository、Storage Router 或跨存储一致性合同。
