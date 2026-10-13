# RAG V4 子方案 06：Chunk 模型与 LightRAG 图谱查询合同

## 0. 已确认决策

本方案建立在 05 子方案之上，并采用以下实现前提：

1. 本次是完整重构，没有线上调用方和历史数据兼容要求；
2. `DocChunk` 一次性重命名为 `Chunk`，不保留兼容别名；
3. `retrieval_context` 一次性改为普通 metadata 中的 `contextual_context` 键，通过 `retrieval_text` property 组装；
4. Chunk 不再保存 `node_ids` 或 `extracted_node_ids`；保留普通 `dict` metadata 作为 pipeline 阶段间传递载体；
5. `ChunkHit` 暴露 Chunk 关联的图节点候选；
6. 图节点候选只暴露 Entity，不把 Relationship 作为 Chunk seed 候选；
7. 图谱查询对齐 LightRAG 的 `local`、`global`、`hybrid` 语义；
8. `mix` 不属于 GraphRetriever，由 RAG 外层组合 Chunk 混合检索和图谱检索；
9. 父块展开由工具参数控制，默认关闭；
10. 图节点候选读取失败时返回空列表，最终 Response 层统一移除空值字段；
11. Document revision 增加 `graph_enabled`，默认值为 `True`。

## 1. 模型分层

### 1.1 持久化 Chunk

`Chunk` 只表达 Markdown revision 中的文本和结构事实：

```python
@dataclass(slots=True)
class Chunk:
    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None
    section_path: list[str]
    raw_text: str
    token_count: int
    anchor_labels: list[str]
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        context = str(self.metadata.get("contextual_context") or "").strip()
        return f"{context}\n\n{self.raw_text}" if context else self.raw_text
```

约束：

- `raw_text` 是唯一正文事实；
- `metadata["contextual_context"]` 只影响 Dense、BM25 和 embedding 使用的 `retrieval_text`；
- `metadata["contextual_context"]` 不改变 Chunk 身份、顺序、revision 或图谱来源；
- `metadata["entities"]` 和 `metadata["relationships"]` 随启用图谱的 Chunk 持久化；来源就是所属 Chunk；
- Chunk 不依赖 GraphStore、Neo4j、Qdrant 或 ACL 才能构造。

### 1.2 ChunkHit

`ChunkHit` 是经过 active revision 和 ACL 过滤的检索结果，并携带可选的 Entity 候选：

```python
@dataclass(frozen=True, slots=True)
class ChunkHit:
    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    score: float
    graph_nodes: list[GraphNodeReference] = field(default_factory=list)
```

`graph_nodes` 的语义是“模型可选择的候选”，不是已经确定的 traversal seed。候选为空是合法结果，不表示 Chunk 检索失败。

### 1.3 LightRAG 对齐的图谱事实

图谱抽取和存储采用 LightRAG 的实体/关系语义：

```python
@dataclass(frozen=True, slots=True)
class GraphEntity:
    entity_name: str
    entity_type: str
    entity_description: str


@dataclass(frozen=True, slots=True)
class GraphRelationship:
    source_entity: str
    target_entity: str
    relationship_keywords: str
    relationship_description: str
```

RAG 可以使用自己的稳定 `node_id` 和 `edge_id` 作为本地索引身份，但字段含义必须保持一致：

- `entity_name` 对应实体名称；
- `entity_type` 对应实体类别；
- `entity_description` 对应实体摘要；
- `relationship_keywords` 对应关系检索关键词；
- `relationship_description` 对应关系语义描述。

`GraphNodeReference` 至少暴露：

```text
node_id
entity_name
entity_type
entity_description
```

它只用于模型选择 seed，不替代完整 GraphEntity，也不携带垂类 metadata。

## 2. Chunk 到 Entity 的来源关系

图事实的来源 Chunk 由其所在的持久化 Chunk 隐含表达，不建立单独的 `GraphChunkSource` 事实集合。`ChunkHit.graph_nodes` 直接从该 Chunk 的 Entity metadata 生成；Relationship metadata 同样带有稳定 edge ID。

为支持图查询回到来源文本，GraphIndexer 将来源 Chunk 关联写入 Neo4j 图投影。GraphRetriever 从遍历结果收集来源 Chunk ID，再按 resource/revision 批量读取 Mongo Chunk，最终校验 active revision 和 ACL。Neo4j 丢失时，可从 Chunk metadata 重建来源关联，不调用 LLM。

## 3. 三种 LightRAG 图谱查询模式

### 3.1 `local`

`local` 面向实体附近的局部知识：

```text
query or selected entity seeds
  -> entity vector / entity lookup
  -> entity descriptions
  -> related relationships
  -> source Chunk associations from graph projection
  -> Mongo Chunk lookup
  -> ACL + revision filter
  -> bounded graph result
```

它优先使用 Entity 检索和实体邻接关系，适合回答围绕具体对象、人物、概念或资源的问题。

### 3.2 `global`

`global` 面向关系和整体知识：

```text
query
  -> relationship keywords/vector
  -> relationship descriptions
  -> endpoint entities
  -> source Chunk associations from graph projection
  -> Mongo Chunk lookup
  -> ACL + revision filter
  -> bounded graph result
```

它优先使用 Relationship 检索，不要求先由 Chunk 提供 seed。

### 3.3 `hybrid`

`hybrid` 同时执行 local 和 global 两条图谱路径，再在统一候选池中去重、排序和裁剪上下文预算。它不是 Chunk hybrid retrieval 的别名，而是图谱内部的实体路径与关系路径组合。

### 3.4 `mix` 的责任归属

`mix` 不进入 `GraphRetrieveRequest.mode` 的合法值。RAG 外层通过编排组合两种能力：

```text
Chunk hybrid retrieval
  -> model selects graph_nodes
  -> GraphRetriever(local/global/hybrid)
  -> combine Chunk evidence and graph result
```

这样可以保持 Chunk 混合检索独立运行，也避免 GraphRetriever 同时承担两套候选池和排序合同。

## 4. 工具接口与父块展开

### 4.1 混合 Chunk 检索

工具请求增加：

```python
expand_parents: bool = False
```

默认响应必须返回 Chunk 命中及其可选 `graph_nodes`；当前只返回父块的响应结构需要改为同时支持 Chunk 结果：

```python
class RetrieveHybridResponse:
    hits: list[ChunkHitResponse]
    parents: list[DynamicParentResponse]
```

当 `expand_parents=True` 时，RAG 才按 section 和匹配 Chunk 组装 `DynamicParent`；当开关关闭时，`parents` 为空，并在最终 Response 层移除。

工具路径为：

```text
query
  -> dense + BM25 Chunk retrieval
  -> fusion
  -> active revision + ACL filter + Mongo Chunk lookup
  -> rerank
  -> optional graph_nodes from Chunk metadata
  -> ChunkHit.graph_nodes
  -> optional parent expansion
```

父块展开不改变 Chunk 命中、节点候选或排序结果。Response 序列化阶段删除空列表和其他明确配置为可省略的空字段。

### 4.2 模型选择 seed

模型只能从返回的 `ChunkHit.graph_nodes` 中选择 node ID，并将其放入后续 `GraphRetrieveRequest.seed_node_ids`。GraphRetriever 必须重新校验：

- node 是否属于请求允许的 resource；
- node 是否属于请求固定的 content revision；
- node 是否仍关联所选 resource/revision 下的来源 Chunk；
- node 是否通过当前本地 ACL；
- node 是否符合 graph query 的类别和数量限制。

未被模型选中的候选节点不得自动进入 traversal。

## 5. 实施顺序

### 06-A：重建文档与 Chunk 模型

- `DocChunk` 全量改名为 `Chunk`；
- 删除旧的任意 Chunk metadata builder/codec、`node_ids` 和 `extracted_node_ids`；
- 将 `retrieval_context` 改为普通 metadata 中的 `contextual_context` 键；
- 将 `get_retrieval_text()` 改为 `retrieval_text` property；
- 同步 Mongo entity、Qdrant payload、repository、document preparation 和所有调用方。

### 06-B：建立 ChunkHit 节点候选合同

- 增加 `ChunkHit.graph_nodes`；
- 从已授权 Chunk 的 `metadata["entities"]` 生成 Entity reference；
- 删除 HybridRetriever 从 Chunk 直接读取 `extracted_node_ids` 的逻辑；
- 图谱读取失败时返回空候选并记录原因；
- 父块展开由 `expand_parents=False` 控制。

### 06-C：重建 LightRAG 图谱事实模型

- 将节点语义统一为 Entity；
- 将边语义统一为 Relationship；
- 对齐实体/关系抽取字段和来源 Chunk 关联；
- 保留 RAG 自己的 revision、ACL 和本地 ID 外壳；
- 不复制 LightRAG 的运行时或存储实现。

### 06-D：拆分 local/global/hybrid GraphRetriever

- `local` 使用实体路径；
- `global` 使用关系路径；
- `hybrid` 合并两条图谱路径；
- 删除 GraphRetriever 中 `mix` 语义；
- 将 Chunk hybrid + graph query 的组合放入 RAG 外层 capability。

## 6. 验收证据

1. 新架构中不存在 `DocChunk`、`retrieval_context`、`get_retrieval_text`、`node_ids` 和 `extracted_node_ids` 的业务依赖；
2. Chunk 在没有 GraphStore 时仍可构造、索引和完成普通混合检索；
3. `ChunkHit.graph_nodes` 只包含同 resource/content revision 下的 Entity 候选；
4. 图节点候选读取失败时，ChunkHit 正常返回且 `graph_nodes=[]`；
5. `expand_parents=False` 时不生成 DynamicParent；
6. `expand_parents=True` 时父块由 Chunk 命中重新组装，不改变 Chunk 命中和节点候选；
7. 模型选择的 node ID 经过 resource、revision、ACL 和来源关系校验；
8. GraphRetriever 只接受 `local`、`global`、`hybrid` 三种模式；
9. `local`、`global`、`hybrid` 分别具备实体路径、关系路径和两者合并路径；
10. `mix` 由 RAG 外层组合 Chunk 混合检索与图谱查询，不进入 GraphRetriever；
11. GraphEntity、GraphRelationship 和 GraphNodeReference 的语义与 LightRAG 对齐；
12. `graph_enabled=False` 时仍可完成 Chunk 检索；它只阻止本次新的图谱抽取，不影响已有图谱投影和候选节点读取。
