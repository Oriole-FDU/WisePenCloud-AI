# RAG 服务重构总览

这份文档是 RAG 服务的入口。它把各专项设计串成一条完整链路；字段和算法细节以对应专项文档为准，不在这里复制第二份定义。

架构分层、能力编排和开发优先级见 [架构与开发顺序.md](架构与开发顺序.md)。

## 1. 目标和边界

RAG 服务是一次全量重写。Common 只提供无业务含义的文档结构事实，RAG 在此基础上定义自己的 `Document`、`DocChunk`、图谱事实和检索结果契约。Common 的 `Section`、`Page`、`Anchor`、`SourceSpan` 可以有限复用，但不作为 RAG 的整体服务契约。Common 的局部 Section ID 会被重写为包含资源和 content revision 的 RAG 全局 ID。

首版明确不做：静态父块、任意 Range 读取、PPR/贡献聚合、Redis 内容缓存、Kafka offset 状态、逻辑删除回滚状态机，以及用 MCP 反向定义 RAG 内部能力。Kafka transport 本身已接入，但不把 offset 写入 RAG 状态。

## 2. 一条完整数据链路

```text
上游资源 + ACL
  -> 解析后的 Markdown
  -> Document（内容、结构、metadata、ACL）
  -> DocChunk（检索原子）
  -> contextual_prefix / key_terms 增强
  -> Dense + BM25 文本索引
  -> LLM 图谱抽取或垂类确定性 producer
  -> Mongo GraphNode / GraphEdge / TextGraphEvidence
  -> 独立补建 Neo4j 拓扑 + 图谱 Node/Edge 向量投影
  -> active revision 发布
```

文档写入不是边写边可见：新 `content_revision` 先进入 staged，完成 Document、DocChunk 和文档检索投影后才通过条件更新成为 applied。图谱是附加能力，Mongo 图谱事实、Neo4j 拓扑和图谱向量均在发布后独立补建；它们失败不会改变 active 指针，也不会影响混合检索。

## 3. 核心领域模型

领域模型定义在 [领域模型.md](领域模型.md)：

- `Document` 是当前内容和结构的聚合根，运行时持有 Markdown、Common 结构、强类型 metadata 和 RAG ACL 投影；ACL 在独立仓储维护，不与内容 revision 一起冻结；
- `DocChunk` 是小粒度检索原子，保存原文坐标、增强产物和 `extracted_node_ids`，不保存静态父块；
- `get_semantic_text()` 和 `get_lexical_text()` 在调用时生成 Dense/BM25 输入，不升级为持久化字段；
- `GraphNode`、`GraphEdge` 与 `DocChunk` 正交；LLM 图元通过 `TextGraphEvidence` 回到 Markdown，确定性 producer 直接从强类型事实生成图元；
- Ontology、metadata filter compiler 和确定性 producer 是插件扩展点，不把论文字段写进通用模型，也不为确定性来源创建 Evidence resolver。

## 4. 持久化和发布一致性

持久化边界定义在 [持久化.md](持久化.md)。

Mongo 保存一个 revision 的正文、结构和 metadata，以及独立的 `doc_chunks`、`text_graph_evidences`。`resource_index_states` 只保存：

- `staged_content_revision`：正在构建的一版；
- `staged_document_version`：构建版本的单调判断依据；
- `applied_content_revision`：线上唯一可见版本。

ACL 的来源权威是上游 `wispen_resource_items`；RAG 本地 `resource_acls` 按 `acl_revision` 异步同步，用于查询候选资源确定后建立一次在线权限快照；Qdrant/Neo4j 的 ACL 仅用于预过滤。ACL 缺失拒绝返回，允许短暂传播延迟。

正常读取不拆 source part，不逐条跨库 IO。Chunk 和 LLM 文本 Evidence 按资源、版本批量回查；确定性图元直接按 active revision 和 ACL 过滤。Redis 首版不引入，除非监控证明 Mongo 组装已经是显著瓶颈。

## 5. 混合检索

完整设计见 [混合检索.md](混合检索.md)。

```text
Qdrant Dense Top-N ─┐
                    ├─ chunk_id 并集去重
Qdrant BM25 Top-N ──┘
  -> Mongo 批量回查当前 DocChunk + Document，并建立 ACL 快照
  -> reranker + Common 相关性门控
  -> ChunkHit
  -> 动态构建不截断的父块
```

Dense 输入是标题路径、contextual prefix 和正文；BM25 输入是标题路径、key terms 和正文。reranker 只看标题和权威正文。

`ChunkHit` 由回查后的当前 `DocChunk` 和 reranker 分数构造，`node_ids` 来自该 Chunk 的 `extracted_node_ids`。它们与具体 `chunk_id` 绑定，供调用方选择后续图谱 seed。

## 6. 图谱检索

完整设计见 [图谱检索.md](图谱检索.md)。图谱检索不是直接返回子图，而是：

```text
Low：节点 Dense 召回
High：关系 Dense + BM25 召回
Hybrid：Low 和 High 并行
  -> Neo4j 有限遍历和过滤
  -> LLM 图元回查 TextGraphEvidence 和 Chunk
  -> 确定性图元直接校验 active revision 和 ACL
  -> 候选粗排
  -> 只对前 N 条做 reranker
  -> Top-K Chunk / 确定性图事实
```

调用方可以只传 `query`，也可以传 `seed_node_ids + query`。支持 `direction`、`relation_types`、`max_depth`、资源和垂类 metadata 过滤。LLM 抽取必须回到 Chunk；论文作者、机构、引用等确定性事实直接作为图事实检索，不强制伪造正文引用或 metadata Evidence。

Ontology 是垂类图谱的核心：插件声明合法实体、关系端点、metadata 事实 producer 和过滤投影。论文 `CITES` 只是示例，不是通用模型的一等公民。

## 7. 标题树

完整设计见 [标题树.md](标题树.md)。标题树只保留两个动作：批量 `neighborhood` 直接接收一批全局 Section ID，可以跨资源返回各自的局部视图；`global_outline` 按资源返回有限深度大纲。

结果使用 JSON 字段描述 current，用一个 Markdown `outline` 字符串表达目录。current 标记 `[C]` 且不在目录中重复自身 ID；父、兄弟和孩子保留 `{#section_id}`。`[+N]` 只表示节点还有 N 个直属孩子未展开，不再有投影森林、`matched` 或横向 gap。

## 8. 读取

完整设计见 [读取.md](读取.md)。读取只有 Page 和 Section 两种方式：

- Page 返回 `resource_id + [{page_label, content}]`；
- Section `DIRECT` 按一批全局 `section_id` 返回轻量 Section 列表，允许跨资源调用；
- Section `RECURSIVE` 同样只依赖全局 `section_id`，返回请求根的资源、完整路径和按真实 Markdown 标题层级拼接的阅读文本，`max_depth` 控制展开深度。

读取不返回 span/offset，不提供 Range 接口。所有正文来自当前 active `Document.raw_content`，读取候选确定后批量建立一次 ACL 快照。

## 9. 查询时的统一原则

无论是混合检索、图谱检索、标题树还是读取，都遵循同一顺序：

```text
候选或 Section 定位
  -> Mongo 批量建立 active/ACL 快照
  -> 当前事实批量回查
  -> 业务排序/拼装
  -> 返回
```

Qdrant payload、Neo4j 属性、标题树视图和动态父块都不是权威来源。MCP、HTTP 或上层工作流只能调用这些明确能力，不能改变其内部数据边界。

## 10. 实施顺序

1. 先实现领域模型、Mongo revision/ACL 仓储和 staged/applied 发布状态。
2. 再实现 DocChunk 生成、增强产物和 Qdrant Dense/BM25 入库。
3. 实现混合检索、回查、相关性门控和动态父块。
4. 接入图谱 Node/Edge、LLM TextGraphEvidence、Ontology/确定性 producer 插件，以及发布后独立的 Neo4j/Qdrant 图谱投影。
5. 实现统一标题树渲染、三种入口和读取 Page/Section。
6. 最后接入上层工具编排；工具层不反向扩张 RAG 契约。

每一步都以当前 active revision、请求级 ACL 快照、批量跨库 IO 和可重建索引为验收底线。

## 11. 旧代码复用清单

旧代码不是实现契约，但其中已经验证过的行为应直接保留：

| 能力 | 处理方式 |
|---|---|
| ACL `can_read()`、VIEW 权限位、用户/群组例外和排除规则 | 直接复用判断语义及测试；存储改为本方案的 `resource_acls` 投影 |
| 权威 ACL 读取、`save_if_newer`、同 revision 重试 | 复用读取与版本比较行为；不要让索引 ACL 取代最终判权 |
| OpenAI/Embedding/Reranker 客户端的超时、重试、关闭和错误映射 | 复用客户端边界；业务层不自行创建一套调用协议 |
| contextualize、关键词提取的提示词、并发限制和缓存键 | 复用已验证的调用策略；结果只写入 `DocChunk` 增强产物 |
| 图谱连续窗口、坐标映射、LLM TextGraphEvidence 校验、稳定 ID 去重 | 复用算法和校验顺序；抽取协议改为 Instructor + Pydantic + OpenAI |
| staged/applied 条件更新和批量写入 | 复用并落实到 [持久化.md](持久化.md) 的发布流程 |

以下旧设计明确不带入：MCP 章节对内部能力的反向约束、静态 ReadingBlock、任意 Range/图谱证据读取接口、PPR/贡献聚合、`mention_count`/置信度累加、Kafka offset 状态和逻辑删除回滚状态机。它们不是遗漏，而是当前边界下的主动删除。

## 12. 事件与失败处理

事件只携带资源标识、内容版本和文档版本；消费时重新读取权威资源，不把消息 offset 当作 RAG 状态。收到旧文档版本时直接忽略，收到同版本时允许幂等重试。

删除采用“先不可见、后物理清理”：先清空 `staged/applied` 指针，再批量删除 Mongo、Qdrant、Neo4j 数据。删除操作必须幂等；单库失败记录可重试任务，超过重试次数进入 DLQ 并报警，不恢复 active 指针，也不把半删除状态返回给查询。
