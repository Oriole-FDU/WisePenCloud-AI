# P5 第四片：图谱检索、一跳 traversal 与 Chunk 实体候选

记录日期：2026-10-13。基线 `dev/common` / `2ff629a7`，本片改动未提交；保留 WisePenView 与 LightRAG 两个既有子模块状态。承接 24 的组件能力，实际装配两个 application 用例，不接 Container/main、MCP transport 或在线消费者。

## 已实现的用例与结果

- `application/graph/retrieval.py`：GraphRetrieval 每次调用使用 GraphRetriever.from_storage 与 Common RetrievalPipeline，支持 local/global/hybrid；默认 hybrid、top_k=10、候选总数 60、每路 30。query 直接 embedding 一次，不生成关键词。逻辑种子按 `(kind, fact_id)` 去重，首个有效来源决定顺序、分数和 MMR 向量。
- `application/graph/traversal.py`：GraphTraversal 接收逻辑 node_ids，一跳读取，不提供 depth。先以当前任意合法可见来源确认种子，不绑定旧 ChunkHit。调用方看到第一跳后再选下一种子；None 方向为 BOTH，None 谓词不限制，空谓词列表不读关系。输入去重保序，空 IDs 无 I/O。
- `application/graph/evidence.py`：共用 GraphEvidence、GraphHit、GraphSourceChunk、实体视图 GraphEntity 与公开 EvidenceBuilder / GraphEvidenceLoader。Loader 只在一次调用中合并来源读取和解析，不是跨请求缓存。每组事实集合单独维护，共享读取不能扩大 global 的端点来源范围。
- GraphEvidence 按种子返回实体、关系和来源；事实描述不汇总。global 只包含选中关系及支持该关系的同来源端点。local 和 traversal 返回实体自身合法来源与已选一跳关系的同来源端点。全部事实可以定位到组内完整 raw_text，来源不携带 contextual_context 或其他业务 metadata。
- `application/retrieval.py`：ChunkHit.seed_entities 默认空列表；GraphNodeReference 包含 id/relations，id 使用现有核心身份。HybridRetrieval 不依赖 Ontology，从最终已授权命中生成候选，不增加图库或模型 I/O。补充解析错误记录日志，普通命中保留。

## 授权、边界与失败

向量定位先核对来源、资源/revision、fact_id、gvec 记录 ID 和当前事实输入的 fact_sha256 / graph_version；确认合法种子后才读拓扑。拓扑返回的是定位候选，全部来源用同一 ResourceSnapshotReader 授权回查，再用 parse_graph_facts 读取当前 Mongo 事实。Neo4j 的旧名称、描述和关键词不作为业务响应。

两个图列表都缺失表示无图事实；合法空列表或已删除事实使旧候选失效。部分缺失、结构损坏或 Mongo 来源不符异常传播，读取不套用本体规则，缺失合法端点的关系逐条丢弃。底层 Neo4j adapter 已保证投影结构、拓扑身份和单一来源，application 不再复制这套校验或捕捉同类异常。图读取、内容读取、embedding 和取消异常传播；只有 Common 已有重排降级和普通命中的图候选补充允许降级。

来源 filters 只支持 resource_id/content_revision 的 EQ/IN 和 AND/OR/NOT；用例入口只检查业务字段/操作范围，表达式结构与类型由数据库 adapter 校验，在向量、图库与当前 Mongo 来源上保持一致。该条件不是授权决定。

一跳默认批量预算 100 个逻辑关系候选，完整返回已选关系的合法来源，不按来源条数截取；过滤后不补读取。种子数量与关系预算控制检索范围，不保证返回所有可见关系。不使用全图 degree、来源数或跨来源摘要排序，不裁剪原文或描述，缓存/懒读取与 token 预算仍归 Chat。

关系索引文本使用端点名称、类别及类别业务说明，不包含端点自身的来源描述。关系描述/关键词等事实输入改变会使旧向量的事实摘要失效，新的抽取版本也会使旧向量失效；仅端点来源描述更新时，关系候选仍可有效，但返回端点使用当前 Mongo 描述。

## 本轮用户修正

Ontology 只约束写入与缓存，GraphRetrieval / GraphTraversal / HybridRetrieval 均不依赖它。结构解析由 TypeAdapter 一次完成，不重复手写 Python 类型、过滤表达式形状或底层投影身份检查；用例只约束检索业务范围、授权来源、revision 和事实是否过期。

Ontology.create_node 根据类别生成 GraphNode.relations，每项 GraphRelationOption 为 relation_type/direction；空端点限制适用于任意类别，两侧都允许时为 BOTH。提示随实体事实持久化，不依赖查询侧本体注册表、不查隐藏邻域，也不表示可见事实存在。模型可以据此选择方向/谓词，traversal 查不到则返回空 relationships；查询不按提示反向限制谓词自由。

ChunkHit 引用只含 id/relations；GraphEvidence 中 GraphEntity 再含 source_id/description，移除冗余的 name/ontology_name/category。底层 GraphNode 保留生产及索引需要的原始名称、类别和本体，并新增关系提示；仍是轻量 dataclass，不增加 Mongo 集合。

图向量增加 fact_sha256 与 graph_version payload，查询据当前事实输入核对过期，无需查询侧 Ontology 重渲染索引文本。text_sha256 保留实际索引文本记录。旧向量缺少事实摘要不能成为种子，projection-only 可重建；旧实体缺少 relations 时视图为 []。抽取策略升级为 v2，普通事实 repair 会重算旧成功指纹以补齐提示；仅投影不会改旧 Mongo 实体。

## LightRAG 参考与适配

固定参考 commit `453dce83d6d0354a06e46c8d4029a0895c4e054b`，`lightrag/operate.py`：

- `_get_node_data`：实体向量召回与批量节点读取。
- `_find_most_related_edges_from_entities`：实体邻接关系批量读取。
- `_get_edge_data`：关系向量召回与关系批量读取。
- `_find_most_related_entities_from_relationships`：关系的两个端点读取。

本项目复用这些材料组织方向，不引入 LightRAG runtime；不复制其端点排序形成的无向身份、degree 排序或全局节点描述。核心 ID 保留本体/类别/规范化名称、关系方向与谓词，各来源独立；扩展前后均回查 Mongo 与资源快照，关系端点只采用同来源事实，排序单位为逻辑种子的证据组。没有真实 LightRAG 对照部署，不能据本片测试宣称两者完整输出等价。

## 实际验证与后续

最新定向测试：图 application、普通 HybridRetrieval、事实模型、抽取/索引与既有 Retriever 回归 **269 passed**。覆盖真实快照逻辑与 BSON 往返内存 Mongo adapter、GraphIndexer 实际向量记录到 application 的闭环、模式与排序/MMR、跨来源范围隔离、隐藏/destroyed/旧 revision/过期摘要、来源损坏、逐事实丢弃、来源过滤、取消、并发请求、两次显式一跳、反向边/自环/逻辑关系预算及 ChunkHit 补充降级。

完整 RAG 回归 **813 passed / 25 skipped**（21.59 秒）；Common retrieval 与 ai_clients 回归 **51 passed**（3.73 秒）。修改 Python 文件 Ruff check/format、compileall 与 git diff --check 通过。graph demo 的 Python 片段均编译通过，并用实际 JSON 本体验证文稿中的 works_at/outgoing 提示。真实 Mongo/Neo4j/Qdrant/Kafka 仅由显式配置的隔离 fixture 控制；本次不调用真实模型，不运行业务 repair，不启动服务入口。

下一切片由实际业务装配决定；mix 是否排期待定。任意多跳、父块、确定性 Producer、生产配置/消费者与历史 revision/分块变更后的全面回收不在本片。
