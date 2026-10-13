# RAG V4 子方案 08：Ontology 约束与 LightRAG 图谱装配

## 0. 已确认决策

1. 图谱抽取采用 LightRAG 的 Entity/Relationship 语义，但必须受到现有 ontology 约束；
2. ontology 同时参与 Prompt 约束、结构化输出校验和业务层硬校验；
3. 未知 Entity 类型、Relationship 类型或非法端点关系被丢弃，保留同一 Chunk 中的合法事实；
4. Entity 的基础身份使用 `ontology domain + entity_type + normalized entity_name`；
5. Relationship 默认按 ontology 的 source/target 类型保存为有向关系；
6. Chunk metadata 保存来源事实；图查询需要的来源 Chunk 关联作为 GraphStorage 投影的一部分维护，但 Chunk 检索只暴露 Entity 候选；
7. 确定性 Producer 和 LLM Extractor 统一装配到同一 GraphStore，不建立两套互不兼容的图谱模型；
8. ontology 划分不同 Producer 负责的 Entity category 和 Relationship type，原则上避免两条路径竞争同一事实；
9. 确定性 Producer 主要从 Document metadata 和结构事实生成图谱，LLM Extractor 主要补充文本语义；
10. 保留基础 ID 去重和 upsert，不预先设计事实优先级、冲突仲裁或复杂合并机制；
11. LLM 可以补充结构 Entity 的 description，但 description 不是所有节点的必需字段；
12. 实体链接、跨类型归一和跨 revision 合并留到后续能力，不在本子方案扩展；
13. 不给每个组件增加独立 ID；plugin 只增加稳定的 `name` 和 `version`；
14. `plugin_id` 根据 `plugin_name + plugin_version` 的 canonical 字符串生成；
15. PluginRegistry 负责名称冲突、重复注册和 plugin identity 判定；
16. ontology 不增加独立版本号，图谱 projection 记录所属 `plugin_id`。

## 1. Ontology 的三层约束

### 1.1 Prompt 约束

LLM 抽取 Prompt 使用现有 `Ontology` 渲染以下内容：

- 允许的 Entity category 及其说明；
- 允许的 Relationship type 及其说明；
- 每个 Relationship 允许的 source category；
- 每个 Relationship 允许的 target category；
- Entity 和 Relationship 的字段格式；
- 不得创建 ontology 未声明的类型；
- 不得为了满足类型约束凭空创建节点或关系。

Ontology 只限制抽取空间，不替 LLM 判断事实是否在文本中真实出现。

### 1.2 结构化输出校验

LLM 输出先解析为 LightRAG 对齐的结构化结果：

```python
class EntityExtractionResult:
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelationship]
```

在进入 GraphStore 前，必须完成：

- Entity category 存在于 `Ontology.entity_specs`；
- Relationship type 存在于 `Ontology.relation_specs`；
- Relationship 两端 Entity 已被抽取或已在当前 revision 的事实集合中存在；
- source/target category 满足 `allowed_sources` 和 `allowed_targets`；
- Entity name、Relationship endpoint 和描述满足非空及长度约束；
- 同一 Chunk 内的重复 Entity/Relationship 被规范化去重。

### 1.3 业务层硬校验

结构化输出通过后，仍需使用统一 ontology 校验器构造最终 Entity/Relationship。任何绕过 LLM 的确定性 Producer 也必须通过同一校验器。

校验失败按事实粒度处理：

- 丢弃非法事实；
- 保留同一批次中的合法事实；
- 记录 `plugin_id`、resource、revision、chunk 和拒绝原因；
- 不把拒绝原因写入模型可读的 GraphHit。

## 2. 统一 GraphStore 模型

### 2.1 Entity

GraphStore 的节点语义与 LightRAG 对齐：

```text
entity_name
entity_type
entity_description
```

RAG 继续保留本地稳定 `node_id`，但它只是索引和关联身份，不替代 Entity 语义字段。

Entity ID 的基础组成是：

```text
ontology.domain + entity_type + normalized(entity_name)
```

这可以避免同名不同类实体在当前 ontology 范围内被直接错误合并。不同类型最终是否指向同一现实对象，属于后续实体链接能力。

### 2.2 Relationship

GraphStore 的关系语义与 LightRAG 对齐：

```text
source_entity
target_entity
relationship_keywords
relationship_description
```

RAG 的本地边记录可以使用 `source_node_id`、`target_node_id` 和 `relation_type`，但必须由 ontology 校验方向和端点类别。

当前 ontology 的 `allowed_sources` 和 `allowed_targets` 已经表达方向，因此默认保存有向 Relationship。未来若需要对称关系，应增加明确的 ontology 语义，不在本子方案隐式转成无向边。

### 2.3 Source

来源由 Chunk metadata 中持久化的 Entity/Relationship 抽取事实及其所属 Chunk 隐含表达。GraphIndexer 将来源 Chunk 关联写入 Neo4j 图投影；图查询通过关联取得 Chunk ID 并回查 Mongo Chunk，不另设 GraphChunkSource 集合。

## 3. 两条 Producer 路径的装配

### 3.1 Deterministic Producer

确定性 Producer 的信息源包括：

- Document metadata；
- Document structure；
- ontology 允许的 category 和 relation type。

它适合生成结构上确定的 Resource、组织、人员、栏目、文档层级等事实，并为这些事实生成稳定的 Entity/Relationship 和 metadata filter values。

在五类核心抽象中，这些规则由 GraphIndexer 调用，输入来自 Chunk 已携带的结构字段和必要 metadata。Document 级原始信息在准备或分块时传递，不要求 `Indexer.index()` 再接收 Document。

### 3.2 LLM Extractor

LLM Extractor 作为 GraphExtractor Transformer，读取摄入中的 Chunk 和注入的 ontology，负责补充正文中表达的语义 Entity/Relationship：

- 不再使用 `chunk_selector`；
- `graph_enabled=True` 时接收该 revision 的完整 Chunk 列表；
- 只输出 ontology 允许的类型；
- 只为具有文本依据的事实建立 source；
- description 可以为空；
- 可以为已有结构 Entity 补充 description，但不要求所有结构 Entity 都经过 LLM。

结果写入普通 `metadata["entities"]` 和 `metadata["relationships"]`；Transformer 不访问存储，metadata 本身不引入 Pydantic 校验。

### 3.3 统一装配

两条路径最终都转换成同一组 Entity、Relationship，并归属到产生它们的 Chunk：

```text
Chunk raw text/context
  -> GraphExtractor.transform(chunks)
  -> Chunk metadata["entities"/"relationships"]

Chunk metadata + 结构字段
  -> GraphIndexer.index(chunks)
  -> Deterministic Producer 生成结构关系
  -> ontology validation + simple ID dedup
  -> ChunkFactsIndexer persists Chunk facts and metadata
  -> GraphIndexer projects Entity/Relationship topology and source Chunk associations
  -> Neo4j topology / Entity+Relationship vector projections
```

不建立 producer 优先级、冲突事件或复杂事实合并状态。相同稳定 ID 的重复写入必须幂等；字段协调只采用最小的确定性 upsert 规则，不扩展为事实仲裁系统。

## 4. Plugin 身份与注册表

### 4.1 Plugin 字段

`RagPlugin` 增加：

```text
name
version
plugin_id
```

`plugin_id` 由 canonical 字符串生成：

```text
plugin_id = hash(normalize(name) + "\0" + normalize(version))
```

实现可以使用固定前缀提升可读性，例如 `plugin_<hash>`。不再要求每个 ontology、producer、filter 或 extractor 单独声明 ID。

### 4.2 PluginRegistry 判定

注册表负责：

- 拒绝空 name 或 version；
- 拒绝相同 name/version 的重复注册；
- 对同名不同版本是否共存作出明确判定，并保证 Document metadata 路由不会产生歧义；
- 按 Document metadata 路由到唯一 plugin；
- 将 `plugin_id` 保留在 Chunk graph metadata、图 projection 和日志中。

Plugin 的 ontology、deterministic producer、metadata filter 和 extractor 配置由 plugin 定义统一持有，组件本身不再成为独立注册对象。

## 5. 图谱失败与查询边界

- ontology 非法事实被局部丢弃，不阻塞 Chunk applied revision；
- GraphIndexer 或 projection 失败时不回滚已经完成的普通 Chunk 投影；基础 Pipeline 按最小合同停止后续阶段并传播未处理异常；
- 已写入的图谱 projection 只允许在完整 revision replacement 或明确的 projection lifecycle 中更新；
- `graph_enabled=False` 只跳过本次新的图谱抽取，不清理既有图谱事实；
- 已持久化在 Chunk metadata 中的 Entity/Relationship 仍可用于重建并参与 local/global/hybrid 查询；
- Chunk 暴露的节点候选仍受 resource、content revision、active revision 和 ACL 校验。

## 6. 实施顺序

### 08-A：Ontology 校验器收敛

- 保留现有 `Ontology`、`EntitySpec` 和 `RelationSpec` 结构；
- 统一 Prompt renderer、结构化输出 validator 和业务校验器；
- 增加事实粒度拒绝原因和可观测日志；
- 删除任何绕过 ontology 的 graph write path。

### 08-B：统一 Entity/Relationship GraphStore 模型

- 将 LightRAG Entity/Relationship 语义映射到统一 graph facts；
- 保留 RAG 本地 node/edge ID、revision、ACL 和 source Chunk 关联；
- 统一 Entity、Relationship 和 source Chunk 关联的 upsert/dedup；
- 不实现事实优先级和复杂冲突仲裁。

### 08-C：装配 Deterministic Producer 与 LLM Extractor

- 删除 `chunk_selector`；
- 让 GraphExtractor 和 GraphIndexer 对 enabled revision 消费完整 Chunk；
- GraphExtractor 只写派生 metadata，GraphIndexer 负责确定性结构装配、归一、去重、ontology 校验和持久化；
- 确定性结构事实和 LLM 语义事实保存在来源 Chunk metadata 中，并由 GraphIndexer 统一装配到 GraphStorage；
- 允许 LLM 补充已有 Entity description，但不要求强制补齐。

### 08-D：Plugin name/version identity

- 为 RagPlugin 增加 name、version 和 hash plugin_id；
- 更新 PluginRegistry 的注册冲突判定；
- 将 plugin_id 写入 graph projection、日志和构建结果；
- 不为每个 plugin component 增加独立 ID。

## 7. 验收证据

1. LLM Prompt、结构化输出和业务写入都受到同一 ontology 约束；
2. 非法 Entity/Relationship 被局部丢弃，合法事实仍能写入；
3. Entity ID 包含 domain、type 和规范化名称，不因 Chunk 来源不同而重复创建；
4. Relationship 的 source/target 类别符合 ontology；
5. Deterministic Producer 和 LLM Extractor 写入同一 GraphStore 模型；
6. 相同稳定 ID 的重复事实 upsert 幂等，不存在额外冲突仲裁状态；
7. LLM 可以为结构 Entity 提供 description，但 description 缺失不会使 Entity 无效；
8. `graph_enabled=False` 不执行新的抽取，但既有图谱仍能查询；
9. 同一 name/version 生成相同 plugin_id；
10. PluginRegistry 可以拒绝非法重复注册和名称冲突；
11. Graph projection、日志和构建结果都能定位到 plugin_id；
12. 不存在独立的 ontology version、producer ID 或 extractor ID 依赖。
