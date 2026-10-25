# P5 第三片：图向量与 GraphRetriever 组件

记录日期：2026-10-12。基线 HEAD 59aa58ef（第二片已提交），本片未提交；保留两个既有子模块状态。以用户实施中的最新决定为准：GraphIndexer
同时负责拓扑与来源向量，不新增独立向量 Indexer；GraphRetriever 负责 local/global/hybrid 种子召回，复用 Common
Pipeline，授权和一跳扩展留在准备阶段，不装配具体图用例。

## 当前边界

- Mongo Chunk metadata 仍是图事实权威，GraphNode/GraphEdge/本体保持 dataclass，没有新的事实集合、Stored 模型或 Chunk 字段。
- GraphIndexer 直接注入 GraphWriter 与两个 VectorWriter（实体/关系各绑定一个 collection），以及向量阶段所需
  EmbeddingClient。本片没有 GraphVectorIndexer，也没有 LocalGraphRetrieval 或 GraphContext。
- GraphRetriever 返回 Candidate[GraphMatch]，payload 只有 GraphVectorKind 和原始 VectorMatch；候选没有授权结论或图描述，不是
  MCP 可直接返回的业务结果。
- local/global/hybrid 当前分别表示实体种子、关系种子、两路融合。完整 LightRAG 查询材料语义尚未实现，不以这三个枚举宣称授权图查询已完成。
- 直接 embedding 调用方 query，不生成关键词。RAG 不设置输出 token 预算、不裁剪正文或描述、不管理结果缓存或懒读取；Chat
  负责这些行为。候选 top_k 是召回范围，不是正文裁剪。
- 多个图用例在 application 准备阶段授权后消费种子，尚未装配；mix 是否排期待定。生产入口、普通发布图装配、历史
  revision/重分块贡献全面回收均留后。

## 本体绑定与校验收拢

Ontology.create_node 绑定 namespace 并检查实体规则，create_edge 绑定单 Chunk 来源并只连接当前接受端点。Ontology.parse_chunk_facts
统一持久化字典解析、来源核对、类别/谓词/端点规则和首次合法去重。结构/来源损坏报错，本体非法单个事实丢弃；strict=True 拒绝非法项及重复
ID，用于成功缓存。

抽取使用本体构造方法，缓存与 Indexer 使用同一解析入口。GraphIndexer 完整输入只解析一次，两个写入层共享结果。没有把 Ontology
对象存入 Node/Edge：asdict 会将普通 dataclass 字段写入事实，而数据库读取本来就需要重新校验当前规则，运行时绑定不能代替外部边界检查。

Instructor DTO 负责响应结构，Ontology 负责语义，来源核对负责 Chunk 归属；Neo4j adapter 仍负责 JSON/payload 与物理图元身份一致性，不持有
Ontology 或 ACL。这些不同边界的校验保留。

## 向量投影与失败

向量按逻辑事实 × Chunk 来源保存。entity_vector_text 包含本体标识/说明、类别标识/说明、名称和当前来源描述；relationship_vector_text
包含本体、谓词/说明、有向两端名称及类别/说明、关系描述和关键词。空可选字段为空文本，不加入其他来源、contextual_context、raw_text
或无关 metadata。

GraphVectorKind、文本模板和 graph_vector_record_id 与图存储合同同在 storage/graph.py。ID 为 gvec_ + SHA-256
(JSON[kind, chunk_id, fact_id])，JSON 使用 UTF-8、ensure_ascii=False、紧凑分隔符；核心 ID 不变。payload 只有
chunk_id/resource_id/content_revision/ontology_name/fact_id/text_sha256，不保存事实描述。text_sha256
供后续授权准备核对实际索引语义是否仍然有效。

GraphIndexer 的 writer 可只启用拓扑，或注入完整向量依赖组只启用向量，也可两者启用；至少启用一种，向量组不可缺项。先完整事实校验，再拓扑
replace_sources，最后来源向量替换。默认来源批 64、embedding 批 32。每来源批全部 embedding 及响应校验成功后，删除两集合中明确
Chunk 的旧记录，再写新记录；空事实也清旧。同资源由调用方串行。

拓扑成功后向量失败不回滚拓扑；当前来源批模型失败不清旧向量；清旧/写入失败可能部分成功，不自动重试或补偿，重跑恢复。模型更换需要调用方保持写读一致并选择新集合或明确重建，不增加在线迁移协议或模型缓存。

Qdrant sparse_vector_name=None 是 dense-only；query (text=...) 在 I/O 前报错。普通 Chunk 的默认双向量行为不变。ensure_collection
(metadata_index_fields=...) 可显式建平面 KEYWORD 索引，图集合使用 resource_id/content_revision/chunk_id/ontology_name；无每次
CRUD 初始化或隐式重建。

## 召回组件与 Pipeline

GraphRetriever.from_storage 接收选中路线所需 reader、embedding client、mode、预算、filters、include_embedding 和可选
Fusion[GraphMatch]。默认 mode=hybrid，每路 30、融合上限 60、Common RoundRobin；可注入 RRF。单路使用总 candidate_limit，不接受
hybrid 路内预算，不要求另一 reader。

每次 query 只生成一个 embedding，并发读取选中集合；请求向量留在局部变量。同来源记录用 kind:record_id 作为融合身份，不按逻辑事实
ID 合并不同来源。单路保持 provider 分数，hybrid 分数由所选 Fusion 决定。embed_query 的数量/维度/有限值校验由普通
VectorRetriever 和 GraphRetriever 共享。模型、后端异常及取消传播。

组件不持有 GraphReader、ResourceSnapshot、Mongo、publication 或 ACL；它不会用未经授权的种子扩展。可直接交给 Common
RetrievalPipeline 的 retriever 参数，prepare_candidates 由实际 application 用例提供。下一片必须在准备阶段核对向量定位、资源/revision、当前
Mongo 事实与 text_sha256，授权后才按需要读取一跳，并再次核对全部来源；不能把候选 payload 或 Neo4j JSON 直接当成事实响应。

## repair 与验证

repair --with-graph 和 --with-graph-vectors 分别选择同一个 GraphIndexer 的 writer，两个开关独立。普通顺序为 applied 读取 →
增量抽取 → 写前状态复核 → 事实保存 → GraphIndexer（拓扑然后向量）。所有启用投影都成功才计 repaired；成功缓存仍写全部投影。

--projection-only 至少启用一种投影，读取已有事实、复核发布状态后直接 Indexer，不创建 InstructionClient、不保存
Chunk、不运行抽取；拒绝 force/gleaning。向量重建仍需要 embedding。配置由 CLI/RAG_GRAPH_REPAIR_* 提供，启用向量时连接前校验
URL、embedding 配置、正维度和不同集合；默认
graph_entity_source_vectors_v4/graph_relation_source_vectors_v4。正常、异常、取消均关闭客户端；资源失败继续、非零退出，不加载
Nacos、不 stage/apply、不重写普通向量。

实际验证：

- 定向抽取/Indexer/repair/retrieval/存储与 Qdrant：255 passed / 3 skipped（新增真实 dense 图测试之前）。
- RAG 全服务：734 passed / 25 skipped；Common Markdown/AI clients/retrieval：125 passed。
- 25 项跳过为 12 Mongo、3 Kafka、3 Neo4j、7 Qdrant；新增 4 个 Qdrant 图向量集成场景需要显式 RAG_TEST_QDRANT_URL，随机双集合，仅清理
  fixture 自身资源。没有真实后端/模型联调或业务 repair。
- 修改的 17 个 Python 文件 Ruff check/format check 通过；compileall、uv lock --check --offline、git diff --check、依赖边界与
  repair CLI help 检查通过。已有无关 contextualizer.py 导入排序不扩大修改。

参考固定 LightRAG commit 453dce83d6d0354a06e46c8d4029a0895c4e054b：operate.py:2744 实体 name+description 向量、关系向量构造、4975
查询关键词、6200 实体路线、6529 关系路线及来源回查。适配差异是增加本体/类别说明、来源独立、有向谓词身份；本片直接查询
embedding，授权拓扑准备后移，不引入其 runtime、KV tracking、模型摘要或 token 裁剪。使用示例见 demo/graph.md。
