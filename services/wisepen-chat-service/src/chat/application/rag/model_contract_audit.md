# RAG 模型与 Tool 契约审计

## 结论

RAG 主流程功能完整，但重构前的数据边界存在以下问题：

1. Tool 输出没有按模型的下一步动作裁剪，内部版本、定位和统计字段大量泄漏。
2. 多个 application dataclass 通过嵌套保留上游完整对象，下游只读取其中一两个字段。
3. 导航状态把图节点 ID 和 Section ID 混在同一个集合中，并丢失 Section 到 Resource 的归属，迫使模型重复提交 `resource_id`。
4. 图抽取投影把审计信息写入 Neo4j，但当前没有查询、鉴权、回源或日志消费者。
5. 部分 Tool 字段不是冗余，而是语义不成立，例如通过返回数量猜测 `truncated`，以及把“本次没有新结果”称为 `exhausted`。

这些问题现已按 Agent 可消费性完成处理：Tool 契约、导航状态、检索 handoff 和图投影模型均已收紧；下文保留审计依据和处理结果。

## Agent 优先原则

RAG 的 Tool 和跨层 handoff 首先服务 Agent，不服务“看起来完整”的对象结构。每个 Agent 可见字段必须明确回答以下至少一个问题：

1. 它帮助 Agent 理解哪条证据或结论？
2. 它让 Agent 可以做出哪一个下一步调用选择？
3. 它如何帮助 Agent 在回答中定位或说明证据来源？

不能回答上述问题的字段留在仓储、权限、日志或 trace 边界，不能进入 Tool 返回。统计字段还必须额外满足：它的值会改变下一步动作，并且它是后端能够证明的事实。

因此：

- 不返回为了“完整”而回显的输入、动作名和内部版本。
- 不返回 Agent 没有操作入口的内部 ID。
- 不把结构化日志、指标和调试审计塞进业务 dataclass。
- 只有提供 cursor 或可继续请求范围时，才返回真实 `has_more`；没有 continuation 的 `truncated` 对 Agent 没有消费价值。
- ACL、revision、偏移量等字段可以保留在内部模型，但只有在它们直接构成证据定位时才以经过裁剪的形式暴露给 Agent。

## Tool 参数审计

RAG 当前只暴露三个 Tool，共 11 个模型可见参数。

| Tool     | 参数               | 当前真实消费                        | 结论     |
|----------|------------------|-------------------------------|--------|
| locate   | `query`          | embedding、召回、精排、导航状态          | 保留     |
| locate   | `max_results`    | 最终检索 `top_k`                  | 保留     |
| sections | `state_id`       | 用户/会话归属和输入白名单校验               | 保留     |
| sections | `section_ids`    | 状态白名单和 Section 读取             | 保留     |
| expand   | `state_id`       | 用户/会话归属和输入白名单校验               | 保留     |
| expand   | `node_ids`       | Neo4j seed 节点                 | 保留     |
| expand   | `query`          | 候选图路径的意图排序；省略时回退 `root_query` | 已修复后保留 |
| expand   | `relation_types` | Cypher 关系过滤                   | 保留     |
| expand   | `direction`      | Cypher 路径方向模板                 | 保留     |
| expand   | `max_depth`      | Cypher 有界路径模板                 | 保留     |
| expand   | `max_results`    | 候选池大小和最终路径数量                  | 保留     |

### 已修复的能力缺失

`expand.query` 原来只回显到 `focus.query`，没有进入 service 或 Neo4j，因此完全不影响结果。这属于多跳意图排序能力缺失，不是普通死参数。

修复后的边界是：

```text
node_ids + relation_types + direction + max_depth + ACL
    -> Neo4j 生成合法候选路径
query or state.root_query
    -> application ranking pipeline 对候选路径排序
max_results
    -> 截断最终路径
    -> 只对入选路径回源证据并更新导航状态
```

自然语言 `query` 不参与 Cypher 条件，不改变图遍历合法性。

## 重构前 Tool 输出审计

### 三个 Tool 的公共冗余

下列字段没有下游程序消费者，模型也可以从 Tool 名称、调用参数或实际列表直接得到同一事实：

- `action`：Tool 名称已经表达动作。
- `root_query`：重复 locate 输入；只需留在导航状态中作为 expand 排序回退。
- `focus`：重复本次 Tool 输入。
- `navigation.visited_nodes`：当前状态混合图节点和 Section，名称与数值语义不一致。
- `navigation.frontier_nodes`：对应的新节点已经出现在结果列表中。
- `navigation.truncated`：当前通过 `len(results) >= limit` 猜测，不能证明后端还有结果。
- `navigation.exhausted`：只能说明本次没有新路径或新 Section，不能证明全局已经耗尽。

`locate` 还固定返回空 `edges` 和空 `paths`；这两个字段应直接删除。

### 节点与边输出

确定冗余或无效的字段：

- `KnowledgeNavigationNode.type_tags`：写入时固定等于单个 `entity_type`，重复表达同一信息。
- `KnowledgeNavigationNode.available_relations`：当前没有任何生产者赋值，永远为空列表。
- `edge.relation_profile`：内部 schema profile，可由 `relation_type` 确定，模型没有操作入口。
- `edge.evidence_ref_ids`：模型不能用该 ID 回源；实际回源使用内部 `evidence_source_ref_ids`。
- `edge.qualifiers`：渲染器固定返回空列表。

`paths` 仍有真实价值：它表达去重后的 `nodes` 和 `edges` 如何组成多跳路径。应保留引用结构，但不重复节点和边正文。

### Section、ReadingBlock 与 Evidence 输出

模型下一步真正需要的是：

- `resource_id`、`section_id`：选择下一次 Section 读取。
- `title`、`section_path`、`summary`、`has_content`：判断阅读价值。
- parent、previous、next、children：继续标题树导航。
- `preview`：大正文未内联时仍可帮助选择。
- 页码和锚点：支持证据定位和回答引用。

应从可见结构移到 `CacheableText.metadata` 的内部定位字段：

- `document_version`
- `reading_block_id`
- `ref_id`
- `content_start` / `content_end`
- ReadingBlock `ordinal`

每段缓存正文应至少携带稳定语义标识：

```text
ReadingBlock: resource_id + section_id + reading_block_id
Evidence:     resource_id + section_id + source_ref_id
```

这样 inline `contents` 和持久化 `content_receipts` 都能通过同一 metadata 识别正文来源，不再依赖易碎的 `content_index`。

## Application Dataclass 审计

### 重构前检索链路的完整对象嵌套

重构前链路是：

```text
RagRetrievalCandidate
  -> RagRankedHit(candidate + complete ranking object)
  -> RagMaterializedHit(hit + reading_block + source)
  -> RagLocatedSection(materialized_hit + section view)
```

主要问题：

- `RagRankedHit.ranking` 在构造后没有生产代码读取；排序后的 tuple 顺序已经表达最终排名。
- `RagLocatedSection.materialized_hit` 最终只被读取 `resource_id` 和 `chunk_id`，却保留整条深层对象图。
- `RagRetrievalCandidate.document_version` 和 `page_labels` 没有 application 消费者。

建议把阶段输出改为最小 handoff：

```text
Ranked retrieval result
  chunk_id / resource_id / section_id
  reading_block_id / source_ref_id

Materialized section hit
  mention_source(resource_id, chunk_id)
  section_view
```

排序分数如果只用于可观测性，应写结构化日志或专用 trace，不应随业务对象进入后续每一层。

### Section 视图的剩余改进空间

`RagSectionView` 仍同时服务 locate、read_sections 和 expand，通过默认空 tuple 表达 `sources`、`reading_blocks`
。这部分未继续拆分，因为拆成三个同构 DTO 会增加转换层，而当前各构造入口已经明确；后续只有出现真实错误状态消费者时再拆。

可选的后续拆分是：

- `RagSectionFrontier`：当前 Section 和轻量邻接结构。
- `RagLocatedSection`：frontier + 单个命中正文/证据描述。
- `RagReadSection`：frontier + 当前 Section 全部 ReadingBlock。

### 已处理：导航状态混合两类 ID

重构前 `KnowledgeNavigationState.known_node_ids` 同时保存图节点 ID 和 Section ID。后果是：

- `read_sections` 和 `expand` 共用一个语义含混的白名单。
- 无法从状态判断一个 Section 属于哪个 Resource。
- 模型必须重复传 `resource_id`，而该值本身不是授权凭证。
- `visited_nodes` 等统计无法区分图节点与文档结构节点。

现已拆为：

```text
known_graph_node_ids: set[node_id]
known_sections: map[section_id, resource_id]
```

之后 `knowledge_navigate_sections` 只接收 `state_id + section_ids`，service 从可信状态解析 Resource，再进行最终 ACL 校验和
Mongo 读取。

### 导航结果携带完整 State

`KnowledgeNavigationLocateResult`、`KnowledgeNavigationExpandResult` 和 `KnowledgeSectionReadResult` 都携带完整
`KnowledgeNavigationState`，renderer 因而可以随意读取内部用户状态字段。

建议每个结果只返回该阶段真正需要的字段，例如 `state_id`、入选路径、Section 视图和新发现 ID。用户 ID、会话 ID、root query
和完整白名单应留在状态仓储边界。

## 图抽取与 Neo4j 投影审计

### 确定没有后续消费者的证据字段

`KnowledgeEvidence` 构造后，下游投影只使用：

- `evidence_ref_id`
- `source_ref_id`
- `chunk_id`
- `quote`

以下字段没有生产代码消费者：

- `resource_id`
- `document_version`
- `page_label`
- `section_id`
- `section_path`

`quote` 是例外：它是 mapper 按 offset 校验过的连续原文，现已进入图投影和 Neo4j，用于组成 Agent 可直接阅读的
`relation_evidence`。offset 只在 mapper 内完成验证，不再进入业务 DTO；其余信息由 `source_ref_id` 指向权威 SourceRef，或只参与稳定
evidence ID 生成。

### 已删除：写入 Neo4j 但从不读取的属性

重构前确认只有写入、没有查询/鉴权/回源/日志读取的属性：

- Mention 的 `evidence_start_offset` / `evidence_end_offset`
- Knowledge relation 的 `evidence_start_offsets` / `evidence_end_offsets`
- Knowledge relation 的 `assertions`
- Knowledge relation 的 `extractor_version`
- Entity 的 `canonical_key`
- External source 的 `source_key`

其中 `extractor_version` 已经参与 `relation_revision`，无需再作为边属性保存。`canonical_key` 和 `source_key` 已经用于生成稳定
`node_id`，当前查询只读取 ID、label 和 entity type。

`relation_profile` 已从抽取结果、图投影、Neo4j 关系属性和 Tool 输出中删除；profile 只保留为抽取 schema 的启用分组。

### 只在测试中消费的结果字段

`KnowledgeGraphIndexResult.projected_node_count` 已删除；只保留 Kafka 日志实际消费的 `projected_relation_count`。

## 必须保留的权限与版本边界

以下字段不是冗余：

- `RagPermissionScope.user_id`、`managed_group_ids`、`joined_group_ids`
- Resource ACL 的 owner、显式允许/排除用户、computed group ACL
- `acl_revision`：防止旧 ACL 覆盖新投影
- `content_revision`：Qdrant 候选、Mongo applied projection 和回源一致性
- `relation_revision`：Neo4j 关系只在当前内容投影下生效
- state 的 `user_id`、`session_id`：拒绝跨用户或跨会话复用 `state_id`
- SourceRef 与 ReadingBlock 的 ID：权威正文回源

这些字段应保留在权限、仓储或内部请求模型中，但不等于要暴露给 LLM。

## 已完成的重构

### 第一阶段：收紧 Tool 契约

1. 删除固定空字段、输入回显和不成立的 navigation 统计。
2. 删除节点 `type_tags` / `available_relations`、边 `relation_profile` / `qualifiers`。
3. 每条边同时返回形式化关系字段和由节点名称、predicate、原文 quote 组成的 `relation_evidence`。
4. 为 RAG `CacheableText` 填充 Section/ReadingBlock/SourceRef metadata。
5. 用 metadata 替代 `content_index` 关联，并补充 inline 与 receipt 两条路径测试。

这一阶段只改变 LLM 可见契约，风险集中且最容易验证模型消费效果。

### 第二阶段：重建导航状态

1. 将 graph node 白名单和 Section 归属映射分开存储。
2. 从 sections Tool schema 删除 `resource_id`。
3. service 从可信状态解析资源后再做 ACL 校验。
4. 删除结果 DTO 中的完整 State，改为阶段最小结果。

这一阶段涉及 Redis 数据结构，应通过新 key 或 schema version 做一次明确迁移，不应静默混读旧状态。

### 第三阶段：压平检索与回源 DTO

1. 删除 `RagRankedHit` 的完整 ranking 对象，保留排序后的最小命中。
2. 删除 `RagLocatedSection.materialized_hit` 深层嵌套，直接携带 mention source。
3. 按 locate/read/expand 拆分 Section read model，消除默认空 tuple 表达的无效状态。
4. 删除 Qdrant candidate 中无消费者的 `document_version` 和 `page_labels`。

### 第四阶段：清理图投影

1. 删除 `KnowledgeEvidence` 中除已校验证据 quote 外的无消费者字段。
2. 删除 Neo4j 只写不读属性及对应 dataclass 字段。
3. 评估并删除可由 relation type 派生的 profile。
4. 为需要保留的审计数据建立明确 trace/metric 消费者，不再把“可能以后有用”当作字段存在理由。

## 验证要求

每个阶段都应同时证明：

- 模型 schema 中不存在未消费参数。
- Tool 结果只包含下一步决策或证据理解需要的字段。
- ACL 在召回、图遍历、Section 读取和证据返回前仍然 fail closed。
- applied content/relation revision 过滤没有弱化。
- 未入选的 expand 候选不会回源，也不会进入 Redis 状态。
- 搜索旧字段名确认生产消费者已经清零，而不是只改 dataclass 定义。
