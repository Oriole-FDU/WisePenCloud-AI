# RAG V4 会话接手文档

记录日期：2026-10-13。本文是下一会话的首读入口，记录当前实现、已确认合同、验证范围与文档整合任务。当前代码基线为 `13d096b4`
；下一会话先完成文档整合，任务和可直接粘贴的开场指令见第 7 节。

实现记录按能力查阅：普通发布/向量/授权读取/检索/章节能力见 17–20，上下文增强与 repair 见 21；图事实、拓扑、向量种子与授权用例见
22–25；平台装配/GC 与类型路由见 26–27。扩展示例初版见 [28](28-employment-extension-implementation.md)
，统一向量协议见 [29](29-vector-index-contract-implementation.md)
，本体条件筛选见 [30](30-graph-ontology-filter-implementation.md)，当前公司内部合同与 metadata
增量合同以 [31](31-employment-department-metadata-implementation.md) 为准。

## 0. 接手先读这些结论

**下一会话的明确任务是文档整合。** 核心组件、通用生命周期与 Employment 完整示例已实现；先把设计、排期、demo
和扩展指南整理为当前合同，保留实施记录的历史语境。父块、在线重构建、历史贡献回收、通用 transport、真实后端/模型联调仍是后续独立工作；mix
排期待定，确定性 Producer 不排期。

Employment 当前场景见 [31](31-employment-department-metadata-implementation.md)：某公司内部合同，上游 metadata 仅
employee/start_date，原样入 Document、逐 Chunk 深拷贝；业务 Transformer 只读校验，不再 model_dump 回写。图为
Person/Department/in_department。专用 factory 只返回 employee/year 增量，通用 VectorIndexer 合并不可被覆盖的 Chunk
resource_id/content_revision；revision 留在 payload 核验，示例不创建其字段索引，三个索引是 resource_id/year/employee。合同
API 也用 employee，返回平面业务 metadata。用户去掉的员工名称规范化保留；大小写/内部空白不归一化。

通用图筛选合同见 [30](30-graph-ontology-filter-implementation.md)：通用图 filters 已支持
resource_id/content_revision/ontology_name 的字符串 EQ、字符串列表 IN 及 AND/OR/NOT。GraphIndexer 来源 metadata 保存绑定
domain；查询从当前实体事实核对本体，不从业务字段或 ID 字符串推断。Employment 图工具只传本体条件和通用参数，不再有二次回查、DTO
校验或证据转换；共享本体来源允许参与，无关系实体保留。旧 Neo4j 来源属性通过 projection-only --with-graph 重建，不自动迁移。

1. **P4 普通内容能力已经完成。** 普通摄入/发布、dense/sparse/hybrid 召回、授权正文回查与重排、目录/邻域/章节原文读取、Contextualizer
   和离线上下文 repair 都有实现和测试。下一会话不应再次提出实现这些能力的计划。
2. **通用平台装配与定时 GC 已完成。** Container/main 已装配默认发布/查询、可选上下文和 Neo4j、共享客户端、固定 Qwen 重排及始终运行的
   GC。Kafka 关闭不影响 GC/查询。settings/main 沿用项目默认 Nacos/单个 lifespan 习惯，没有 models/runtime
   拆分；真实后端/模型联调仍未执行。见 26/27 与 demo/platform.md、src/rag/extensions/extensions.md。源码示例位于
   src/rag/extensions/employment，已取代旧 demo；main lifespan 在共享客户端登记后调用 register_extensions，按 EXTENSIONS
   与源码表构造 extension () 的 Pipeline/额外存储/API 产物，默认空列表不启用扩展。平台保存 app.state.extensions、初始化额外
   Qdrant 集合并纳入共享 GC、挂载 /rag/extensions/{name} 子 API。Employment 展示原样 metadata
   的入职年份/员工索引与部门图关系、授权查询、通用图工具及可信网关身份；默认/扩展集合用 VectorCollection (storage,
   list[IndexableField]) 声明，启动统一调用 VectorStorage.ensure_collection，数据库类型只在后端翻译。合同查询仅映射 filters
   后调用通用 VectorRetrieval (mode=DENSE)，直接返回带完整授权 metadata 的 ChunkHit，不再有专用 prepare
   或业务/文本摘要保护；FROM_SOURCE_SECRET 只在启用 API 时要求。平台 publication 按 Document.type 选择完整注册链，垂类只向
   pipeline_routes 字典赋值，不覆盖 publisher 或接触 Kafka；默认分块深复制业务 metadata。影响摄入的 type/metadata 变化要求提高
   version，路由变更不自动重建。
3. **请求快照归 application，Retriever 只接收查询内容。** ResourceSnapshot 与 ResourceSnapshotReader 两对象已经冻结；不要恢复独立
   resolver、retriever_factory、VectorRetrievalQuery、Retriever 请求缓存或隐式全局 context。
4. **原文和检索派生文本分开。** raw_text 不变，retrieval_text 为路径/上下文/原文；向量投影不能成为正文权威，未经版本/ACL/投影身份检查的正文不能进入响应或外部模型。
5. **P5 图事实与 Neo4j 多来源投影已完成。** GraphStorage/GraphIndexer、来源批次事务、一跳读回与 repair --with-graph
   已实现，向量值对象并入 storage/vector.py。图向量已由 GraphIndexer 统一写入，GraphRetriever.from_storage 支持三种种子路线；授权扩展在
   Common Pipeline 准备阶段，GraphRetrieval / GraphTraversal 已装配，查询不绑定单一本体。mix 排期待定，确定性 Producer
   不排期。见第 5 节与 22/23/24/25。
6. **本文可以独立作为交接输入。** 下文已列出已查清的代码、集合、上游事件与测试事实，不需要为理解现状连接业务数据库、重查 Java
   上游或全库扫描 references。只有当前切片确实缺少的新事实才做定向查证。

## 1. 快速定位与当前基线

- 工作区：`D:\WisePenCloud-AI\wisepen_common\WisePenCloud-AI`，PowerShell。
- 本次交接核对到：分支 `dev/common`，HEAD **`13d096b4`**，提交标题 `feat(rag-v4): 完成rag扩展包`
  。Employment、统一索引协议、VectorRetrieval、ChunkHit.metadata、图本体筛选及 28–31 记录均已提交。本次文档更新前仅有
  `WisePenView` 和 `services/rag/references/LightRAG` 的既有子模块状态；本次仅修改本接手文档，不创建提交。下次重新核对 git
  status/HEAD，保留用户后续提交与子模块状态。
- 新实现只在 `services/rag/src/rag`，测试在 `services/rag/tests`。旧 `services/wisepen-rag-service/src/rag` 与
  `services/rag/references` 只作源码参考。
- 本片开始时只有 WisePenView 和 services/rag/references/LightRAG 既有子模块状态，均保留。Common decision 改动已在
  HEAD；本片不修改 Common/Chat 源码。neo4j 直接依赖在此前切片已落地，本片不修改依赖/锁文件或 Common/Chat 源码。
- **最近实际验证**见 [31](31-employment-department-metadata-implementation.md)：定向 59 passed / 1 skipped，RAG **937
  passed / 29 skipped**，Common retrieval/ai_clients/markdown **125 passed**。修改文件静态检查通过。真实后端 fixture
  因无显式配置跳过；未调用真实模型、业务消费者或业务 repair。Chat 本片未重跑。
- 修改文件 Ruff check/format、compileall、git diff --check、锁文件与依赖边界检查通过；全目录 Ruff check 存在既有
  contextualizer.py 导入排序 I001，HEAD 原文件同样复现，保留该无关文件。

装配关系可直接读 [demo/hybrid.md](../demo/hybrid.md)，其中显式展示 from_storage 的调用位置及完整用例连接。

普通上下文 Pipeline 与离线 repair 的配置/运行方式读 [demo/contextual.md](../demo/contextual.md)。最新材料选择与离线恢复合同以
21 和当前实现为准，不再使用字符预算或 section 内窗口限制。

**文档整合的最短阅读顺序**：本文第 0、1、5、6、7 节 → 31/30/29（当前修正）→ 10/11（主要整合对象）→
demo/platform.md、src/rag/extensions/extensions.md 与 Employment README。需要核实某个合同，再定向读对应源码/测试及 17–28
记录；无需先通读旧 Java 项目、全库 references 或全部历史文档。

文档与决策优先级：用户后续明确决策 > 已确认合同及当前实现 > 10／11 整合计划 > 00–09 历史背景。12–15、17–31
是实施记录，提交状态和验收数字可能是当时的历史值。`架构图.md` 仍含旧目录和未来组件，不能据其判断当前文件存在。当前
GraphWriter.replace_sources/delete_sources 返回 None，无 Receipt。当前结果为 GraphHit (evidence/rank/score)、GraphEvidence
和 ChunkHit.seed_entities；GraphRetrieveRequest / ChunkHit.graph_nodes 属于旧方案，不存在于当前接口。发现真实矛盾要指出并讨论，不能把历史示例自动升级为当前要求。

## 2. 已完成什么

| 能力                          | 当前实际入口                                                                            | 完成范围                                                                                                                             |
|-------------------------------|-----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| 统一内容身份与模型            | `core/schema/content.py`                                                                | ContentRevision、Document、DocumentStructure、Chunk；正文身份与 Common 结构模型                                                      |
| 图事实与本体                  | `core/schema/graph.py`                                                                  | GraphNode／GraphEdge／Ontology 及 specs dataclass；本体支持 JSON/YAML 加载                                                           |
| 图抽取与增量维护              | `ingestion/transformers/graph.py`、`scripts/repair_graph.py`                            | 来源绑定、逐事实硬校验、可选补漏、增量缓存与离线 repair；图投影可选接入，查询用例见 25                                               |
| 图拓扑与来源向量投影          | `storage/graph.py`、`ingestion/indexers/graph.py`、Neo4j/Qdrant                         | 同一 Indexer 一次校验依次写两类投影；repair 可选接入                                                                                 |
| 图种子召回组件                | `retrievers/graph.py`                                                                   | local/global/hybrid 来源候选，Common Pipeline 接口；授权扩展用例见 application/graph                                                 |
| ACL                           | `application/acl/models.py`、`application/acl/synchronizer.py`、`backends/mongo/acl.py` | 本地 VIEW 规则、权威资源刷新、Mongo ACL 投影及 Kafka 消费                                                                            |
| 摄入基础                      | `ingestion/chunkers/markdown.py`、`ingestion/pipeline.py`                               | Common Markdown 适配、三个 Protocol、串行 fail-fast Pipeline                                                                         |
| 可选上下文增强                | `ingestion/transformers/contextualizer.py`                                              | 完整提示词 token 预算、全文/祖先/own/Chunk 窗口、大纲收缩、逐块降级、生成指纹与实例并发限制                                          |
| 离线上下文修复                | `scripts/repair_context.py`                                                             | 显式资源范围、当前 applied 身份与写前状态复核、增量生成及全部向量重写；不 stage/apply，不加载 Nacos                                  |
| 内容事实存储                  | `storage/content.py`、`backends/mongo/content.py`                                       | Document／Chunk 完整对象读写、物理删除、显式唯一索引                                                                                 |
| Chunk 事实 Indexer            | `ingestion/indexers/chunk.py`                                                           | 仅调用 ContentWriter.upsert_chunks，保存最终 Chunk                                                                                   |
| 向量存储合同与通用过滤        | `storage/vector.py`、`core/schema/filters.py`                                           | VectorRecord/Query/Match 与端口；IndexKind/IndexableField/VectorCollection；必填字段列表初始化                                       |
| 向量存储与 Qdrant             | `storage/vector.py`、`backends/qdrant/`                                                 | collection-bound 双向量 upsert/query/delete，显式初始化，completed 确认；真实后端未验证                                              |
| 普通 VectorIndexer            | `ingestion/indexers/vector.py`                                                          | retrieval_text → 分批 embedding → 投影；factory 为业务增量，资源/revision 始终取自 Chunk                                             |
| 发布完整用例                  | `application/publication/publish.py`                                                    | 输入事件、消费者、publish 内部摄入与回查、条件发布、永久销毁                                                                         |
| 通用平台装配/GC               | `container.py`、`main.py`、`application/gc.py`                                          | 共享依赖、显式初始化、消费者握手、固定 Qwen 重排、周期销毁回收与完整清理                                                             |
| 发布状态后端                  | `backends/mongo/publication.py`                                                         | Mongo 条件 stage／apply、永久 destroyed 标记                                                                                         |
| 请求内资源快照与内容读取      | `application/snapshot.py`                                                               | ResourceSnapshot 保存版本／资源授权决定；ResourceSnapshotReader 批量读取 Document／Chunk，身份回查、首次决定复用、部分结果与请求隔离 |
| 普通向量召回                  | `retrievers/vector.py`                                                                  | VectorRetriever.from_storage 初始化预算/filters/向量返回；retrieve 只接收字符串；Common 并发 dense/sparse 与显式融合                 |
| 授权图谱检索与 traversal      | `application/graph/retrieval.py`、`traversal.py`                                        | 三种模式证据组、任意本体事实的授权回查、显式一跳；实体视图暴露 ID 与允许的关系提示                                                   |
| 通用正文检索                  | `application/retrieval.py`                                                              | VectorRetrieval 三模式、授权回查、身份核验、重排/MMR，返回 ChunkHit 与完整 metadata/seed_entities                                    |
| 结构化读取                    | `application/reading.py`、`application/snapshot.py`                                     | DocumentReading.global_outline/neighborhood/read_sections；全局目录、邻域与 own/subtree 精确原文；批量章节定位复用同一资源快照       |
| Common 泛型检索与 Chat 消费方 | Common `utils/retrieval`、Chat 缓存 BM25 工具                                           | Candidate[T]、异步准备、payload 提取器；已移除 RankedCandidate，缓存定位行为保持                                                     |
| 通用 Kafka                    | `backends/kafka/consumer.py`、`retry.py`                                                | 有限快速重试、延迟回投、DLQ、精确 offset 提交及资源清理                                                                              |
| 服务生命周期                  | `container.py`、`main.py`、`api/router.py`                                              | 存储显式初始化、扩展登记/API 挂载、ACL/publication 消费握手、始终运行 GC、就绪与关闭                                                 |
| 扩展装配与公司内部合同示例    | `extensions/__init__.py`、`base.py`、`employment/`                                      | 声明 Pipeline/集合/API，平台统一初始化/GC；员工/年份索引、部门关系与授权工具                                                         |

Contextualizer 与离线 repair 已实现并用替身验收；标题树、邻域、章节 own/subtree、资源快照与普通混合检索已完成。GraphStorage／GraphIndexer
与 Neo4j adapter 已实现；GraphRetriever 种子组件已完成；授权图查询与一跳 traversal 已完成；当前 **没有**
父块；命中后处理接口已移除。普通发布、可选上下文降级和必要向量写入合同通过测试，真实 Mongo/Kafka/Qdrant
尚未验证。离线脚本不是生产在线重构建或生命周期装配。

publication/ACL 消费者已装配到 Container/main，存储显式初始化后等待全部主消费者连接，后台退出撤销 ready。默认 Nacos
settings 现在必须提供 Mongo/Qdrant/embedding/Qwen；ENABLE_CONTEXTUALIZE 和 ENABLE_GRAPH_STORAGE 控制相关可选能力。没有
GC/rerank 开关。永久销毁线上仅撤销可见性，服务内 GC 首轮立即执行并周期按游标清理全部已配置投影、本地 ACL 和全部内容
revision；标记永久保留，迟到写入由下轮回收。真实模型与业务环境不在本片验证范围。

已经完成的两条主链与维护入口：

```text
普通发布：DocumentPublication.publish
  → 核对完整身份 → 条件 stage → 保存 Document
  → MarkdownChunker → 可选 Contextualizer → ChunkFactsIndexer → VectorIndexer
  → 回查 Document 与本次预期 Chunk ID 集合 → 条件 apply

普通检索：VectorRetrieval.retrieve(query, snapshot=..., mode=...)
  → 本次 VectorRetriever.from_storage(mode=dense/sparse/hybrid) → Candidate[VectorMatch]
  → SnapshotReader 授权回查 + 投影身份检查 → Candidate[PreparedChunk]
  → Common rerank/MMR → ChunkHit（正文）
  → 调用方可用 section_id 与同一 snapshot 继续邻域/own/subtree 阅读

离线 repair：显式资源 IDs → 当前 applied 身份读取 → 增量 Contextualizer
  → 写前发布状态复核 → 全部 Chunk 事实替换 → 全部 Chunk 向量重写
  → 报告与退出；不 stage/apply
```

## 3. 用户已确认的边界：不要重新设计

### 3.1 重构与模型

- 在新 `services/rag` 持续实现，不复制旧总 Container、Repository 层或 Snapshot 体系。没有线上兼容、双写、旧 schema 迁移或兼容别名要求。
- Document、DocumentStructure、Chunk 保持三个独立 dataclass；不要统一成 Node，也不要把 Chunk 移回 ingestion。
- 内容模型属于 `rag.core.schema`，不依赖 ingestion、storage、具体后端或 application。`Document` 正文字段实际叫 **`text`**。
- `ContentRevision` 是 frozen、slots dataclass；对原始 text 的 UTF-8 字节计算完整 SHA-256，不 trim、不规范化 Unicode。字符串身份为
  `resource_id@document_version#完整摘要`。
- 内容身份不包含 metadata、Chunker 配置、embedding 模型或图谱配置。已 applied 的同身份提交直接跳过，不借重放更新
  metadata；投影重建须另行定义用例。
- Chunk.raw_text 是正文事实；retrieval_text 按标题路径（">".join）、非空 contextual_context、raw_text
  用空行拼接。路径不转义/截断，无标题省略；展示、章节读取和 rerank 仍用原文。
- Contextualizer 默认完整提示文本 8000 token、目标 100 字、输出硬上限 150 token；TokenCounter 默认 Common 离线
  o200k_base。先全文（省略 metadata/大纲），再最高可容纳祖先 subtree、own 或目标种子；窗口可跨同文档章节，每个 Chunk 只加入一次，不虚构
  offsets。大纲 show_extra_info=False，仅标题骨架，默认不超过输入预算的 15%。
- contextual_context/context_status/context_version 是普通
  metadata。成功指纹含策略/模型/tokenizer/生成配置和实际完整提示词，不含并发数；成功有效且指纹匹配复用，失败/过期重试。模型及输出失败逐块清旧值并
  failed，空目标/预算不足 fallback；输入身份、结构、程序错误和取消传播。必需写入失败仍阻止发布。
- Contextualizer 另外固定 before_ratio=0.5、outline_token_ratio=0.15、max_concurrency=4；并发限制覆盖共享实例的模型调用，文档索引/材料缓存属于单次
  transform。全文模式不使用 metadata，所以只有局部模式的 metadata 变化才通过实际提示词改变指纹；不要擅自把全部 Document
  metadata 纳入全文指纹。
- repair 只处理显式资源当前 applied 未 destroyed revision，核对完整 Document 并读既有 Chunk，写前复核发布状态；维护窗口无其他
  Chunk 写任务。增量生成后保存全部 Chunk、每次重写全部向量，不 stage/apply。模型 failed
  仍写降级事实/投影且退出非零；文档级写入失败继续其他资源，已完成写入保留；关闭全部客户端，不加脚本重试。
- metadata 保持普通字典；不加 metadata schema、codec、注册表或 Stored 镜像模型。图模型写入 metadata 后持久化为字典，读回仍是普通
  metadata。
- ACL 属于独立 ResourceAcl，不混入内容模型或发布状态。版本／冲突／销毁准入不是新增 ACL 鉴权。

### 3.2 Pipeline 与事实存储

- 内部 Pipeline 由开发者固定正确组合， **不增加组件识别、装配校验、依赖图或重复业务检查**。通过实际顺序和失败测试证明合同。
- `run(document)` 返回所有 Indexer 成功后的最终同一 `list[Chunk]`，不复制、不包装。Transformer 可原地增强或返回新列表；后续使用其返回值。Indexer
  只读输入。
- 普通配置固定 `Chunker -> Transformers -> ChunkFactsIndexer -> 全部必需投影 Indexers`。
- 必需 Indexer 只有确认全部所需写入成功才能返回。发布依赖这一成功合同，不新增向量完整性查询、完成清单、Receipt 或投影状态字段来证明完成。
- 任一阶段失败停止后续阶段，已完成写入保留；不回滚、不并行 Indexer、不加 continue_on_error。传输重试属于 Kafka，不放入
  Pipeline。
- Contextualizer 内部已分类的供应商失败会转换为逐块降级并正常返回，这是它的组件合同；不等于所有未来 Transformer
  都可以吞异常。图抽取的模型失败与重跑合同必须在图切片单独讨论。
- ContentStorage 只读写事实，不计算 revision、不推断 active、不管理 publication 或 ACL。写入成功返回 None，异常直接传播。
- ContentStorage 批量读取去重保序、缺失省略；revision Chunk 按 chunk_index 升序；空批量无 I/O。Document 按
  `(resource_id, content_revision)`、Chunk 按 chunk_id 完整替换，每批 500 条有序 ReplaceOne。
- upsert 不删除批次外 Chunk，metadata 不合并。当前不支持同 revision 整版重新分块替换；更改分块配置后的旧 Chunk 清理不能擅自塞进
  upsert。
- Mongo 显式注入异步 collection，显式 ensure_indexes；不在每次写入建索引，不加 Repository／Storage Router 或全局依赖。BSON
  不支持值正常报错，部分成功不补偿。

### 3.3 publication 当前形态

publication 是 application 用例，没有独立于摄入的领域能力。目录已收敛为 **models.py、store.py、publish.py**，另有仅做导出的
`__init__.py`。

`publish.py` 同时定义 DocumentReadyEvent、ResourceDestroyedEvent、PublicationEventConsumer 和 DocumentPublication。消费者只注入一个
publisher 业务对象，内容直接调用 `publish(document)`，销毁直接调用 `destroy_resources(ids)`。

独立 DocumentIngestion 和原 consumer.py／ingestion.py／publisher.py 已移除。发布器不再公开
stage_revision／apply_revision；Store 仍有这些原子状态操作。不要为了恢复之前的分层重新加回这些类、文件或兼容导出。

发布器显式注入 `store: PublicationStore`、`reader: ContentReader`、`writer: ContentWriter`、外部完整
`pipeline: IngestionPipeline`。内容读写可由同一个 MongoContentStorage 同时实现。

```text
Document 身份核对（失败时无 I/O）
  -> Store 条件 stage，保留旧 applied
  -> 无需构建则返回 ALREADY_APPLIED／STALE／DESTROYED
  -> 保存 Document
  -> 外部普通 Pipeline，保存最终 Chunk 和全部必需投影
  -> 回查 Document 身份及精确有序的预期 Chunk ID 列表
  -> Store 条件 apply，清空 staged
  -> 消费成功，允许确认消息
  -> 后续独立构建可选图谱
```

stage：先 destroyed，再低于 staged/applied 最高版本，再最高版本同版本不同摘要冲突，再已 applied 相同身份跳过；同身份 staged
重放允许重建。更高版本替换 staged，旧 applied 保留。

发布前必须读到正确 Document；资源、版本、完整正文摘要和 revision 字符串一致。该 revision 的 Chunk ID 排序列表与本次成功
Pipeline 返回列表完全一致，缺失、多余或乱序均拒绝发布。允许零 Chunk。Common 当前会把只有标题的文档生成一个 flat
Chunk，不强加标题文档必须零 Chunk。

调用方保证同资源构建串行，当前通过主 Kafka 分区消费执行。Mongo 条件更新仍防止新候选或销毁使旧任务错误发布。CAS
失效重新读取只做分类；仍无法完成时抛 PublicationStateChangedError，不在 Store 自动循环重试。不加锁、租约、完成清单、时间戳或跨库事务。

destroy_resources 永久置 destroyed=True，清空 staged/applied；未摄入资源也建标记，每批 500 个有序
upsert。标记不删除、不恢复；正在运行的任务可能留下事实或投影，但不能恢复发布。物理清理由后续用例完成。

### 3.4 请求内资源快照与共享读取

- `application/snapshot.py` 只包含 ResourceSnapshot 与 ResourceSnapshotReader；版本与 ACL resolver 已收为 Reader
  内部方法，后续读取和检索用例统一使用 Reader。
- ResourceSnapshot 绑定可信 PermissionScope，只缓存 `resource_id -> ContentRevision | None`；不保存正文、拒绝原因或跨服务一致性版本。不可见也缓存。
- 每次独立能力调用创建新快照；同一次上层编排的 Document、Chunk 和后续子操作显式共享。共享 Reader 仅保存
  PublicationStore、ResourceAclRepository、ContentReader 固定依赖，不跨用户或聊天会话复用请求状态。
- 只对尚未观察资源批量读 publication，对未 destroyed 且已 applied 的资源批量读 ACL。判权按资源／Document，Chunk 继承；不增加
  Chunk 或 Section 级 ACL。
- 首次完成的版本和可见性决定固定；applied、ACL、destroyed 后续变化不触发回查、切换或重试，新调用重新观察。交错子操作保留最先完成的决定，不引入锁或全局上下文。
- Document 核对 resource、version、revision 和原始 text 的完整摘要；Chunk 核对所属资源与 selected
  revision。事实缺失或身份不符只省略该对象，不回退其他版本。批量去重保序，空输入无 I/O；连接、查询和解码异常直接传播。
- 混合检索 use case 与 HybridRetriever 分开：application 编排资源决定、授权正文回查、重排与组装；Retriever
  只保存固定依赖／配置，retrieve 只接收字符串查询，预算/filters/include_embedding 在初始化时确定；application 每次直接调用
  from_storage 构造组件，不在 constructor 绑定快照或请求 scope。
- snapshot 名称仅表示一次调用内的资源版本与权限决定，不承诺 Mongo／Qdrant／Neo4j 同一时间点，也不缓存正文。

### 3.5 章节身份与结构化读取

- `rag_section_id` 位于 core/schema/content.py：
  `rsec_ + sha256(f"{resource_id}\0{content_revision}\0{common_section_id}".encode("utf-8")).hexdigest()[:24]`。Document
  的章节/父章节与 Chunk/ChunkHit 共用该 ID；不同资源或 revision 不串读，Chunk ID 没有因此变化。
- 调用方直接使用返回的 section_id，不解析 ID，也不另传资源/revision。Mongo 按 structure.sections.section_id
  定位，SnapshotReader 核对 selected revision、Document 版本与正文 hash；旧章节 ID 不切换到新版。没有旧 ID 兼容或存量自动迁移。
- DocumentReading 只依赖 ResourceSnapshotReader，三个接口分别为 global_outline (resource_id, max_level=0)、neighborhood
  (section_ids, sibling_steps=1)、read_sections (section_ids, scope=OWN)，均显式传 snapshot。
- OWN 直接切 Document.text 的 own_span，SUBTREE 直接切 subtree_span；保留标题、空白、中文和 Markdown。不拼
  Chunk、不重建标题、不加窗口/续读/递归深度。范围错误抛 RuntimeError。
- 结果是 DocumentOutline (resource_id, content_revision, outline)、SectionOutline (resource_id, content_revision,
  section_id, section_path, outline)、SectionText (..., text)；section_path 为 list[str]。输入去重保序，缺失/不可见省略；全局目录不可见为
  None，可见无章节为空字符串。非法 scope、负参数在 I/O 前失败。
- OutlineFormatter 默认仍有 ID/offset/anchors/当前标记/summary/preview；Contextualizer 的 show_extra_info=False
  只用于材料标题骨架，不改变正常目录的可继续阅读身份。

### 3.6 Kafka 已定策略

- **不使用或改造 common.kafka**；先前尝试已复原。ACL 和 publication 都使用 `rag.backends.kafka`，后端不导入业务模块。
- 默认快速重试 3 次，等待 1／2／4 秒；随后 `<main>-retry-1m`、`<main>-retry-10m` 各一次；仍失败进入 `<main>.DLT`。正常链最多 5
  次重试、6 次业务执行。
- 坏 JSON／字段、身份不符、同版本冲突直接 DLQ；暂时事实不完整与 I/O 走重试。应用拥有异常分类。
- 延迟 topic 不执行业务：等待到期后把原 value、key、headers 回投原主 topic 的原分区。新版本可以先于旧重试发布，旧重试由版本规则淘汰。
- relay 独立消费，不阻塞主 topic；retry 队列自身逐条等待，不是按资源并行调度器，也不承诺精确到期执行。
- 成功或明确跳过后，提交当前分区 offset+1。转发 retry／DLQ 先确认发送再提交输入位置，发送或提交失败停止循环、异常交由入口重启。基础设施故障不向同一个故障
  broker 递归转发。
- 保留原始消息、key、上游 headers；保留的 rag- headers 携带原
  topic／partition／offset、retry-count、available-at、异常类型／消息／traceback。DLQ 不自动消费，没有自动重放入口。
- producer 用 acks=all，发送确认及请求预算默认 30 秒。未启用 producer idempotence，因为 aiokafka
  在该模式下不会按普通请求超时过期批次；当前采用有界普通发送及应用幂等。
- 语义为 at-least-once，发送与 commit 无 Kafka 事务；崩溃、提交失败或发送超时后仍送达都可能重复。全局业务执行次数不能用正常链预算承诺
  exactly-once。
- 入口必须 await run 并观察异常。主 poll 预算覆盖业务与快速重试；relay 至少覆盖延迟+30 秒。topic 创建、权限、保留期和重启由部署配置负责，库不管理业务
  topic。

## 4. 已查清的实现与上游事实

### 4.1 内容与索引位置

当前 Mongo 内容操作已经收敛在 `backends/mongo/content.py`， **不存在必须继续修改的 chunk／document 子包**。13
记录已按现状修正；早期用户方案指定的路径不要当成当前目录树。集合绑定仍为 `document_revisions` 与 `doc_chunks`，发布状态为
`resource_publications`，本地 ACL 为 `resource_acls`。

内容唯一索引为 Document `(resource_id, content_revision)`、Chunk `chunk_id`、Chunk
`(resource_id, content_revision, chunk_index)`；发布状态 `resource_id` 唯一。TypeAdapter 直接处理原模型与嵌套
ContentRevision。retrieval_text 不持久化，不恢复旧 spans／node_ids。

### 4.2 Common 与旧代码复用点

- Markdown：`services/wisepen-common/src/common/utils/markdown`。RAG 只适配字段与资源/revision 内 ID，不重写解析或分块算法。事件
  to_document 通过 Common 完整分块结果提取 sections/anchors，Pipeline 再正式分块；当前存在这次重复解析，已知且未另造解析接口。
- Instruction：Common InstructionClient 在 `__init__(client, *, model)` 固定模型；complete/structured_complete 只接收本次
  messages、max_tokens 与结构化 response_model。
- Embedding：`services/wisepen-common/src/common/utils/ai_clients/embedding.py` 的 EmbeddingClient 在
  `__init__(client, *, model, dimensions=None)` 固定模型/维度，调用合同为 `embed(texts=list[str]) -> list[list[float]]`
  ，只读 dimensions 供向量消费方核对响应，按 response.index 保序并核对数量。它拒绝空 texts，所以 VectorIndexer 的零 Chunk
  成功路径必须在调用前结束。
- 检索：`services/wisepen-common/src/common/utils/retrieval` 已导出泛型
  Candidate、Retriever、FusionRetriever、RoundRobinFusion、RrfFusion、RetrievalPipeline、reranker／diversity。Candidate 仅有
  candidate_id/payload/rank/score，RankedCandidate 已删除；Pipeline.prepare_candidates 可在重排前异步转换 payload。文本与向量通过
  payload 提取函数提供。
- Common BM25Retriever 为传入的内存 Candidate 列表建索引， **不能当作 Qdrant sparse adapter**。Dense／sparse 的 provider
  召回适配留在 RAG。
- Common RetrievalPipeline 在 reranker 失败时保留原召回顺序，计划已接受此降级行为；需要严格失败合同的用例应明确讨论，不能默认为与
  ingestion 同样 fail-fast。
- 旧向量源码优先看 `services/wisepen-rag-service/src/rag/core/persistence/qdrant/document_vector_repository.py`、
  `graph_vector_repository.py`，旧普通索引编排在 `application/document/indexing.py`。只提取 provider 行为、算法或用例，不复用旧
  repository、发布完整性 API、payload 或运行时。
- 图谱后续参考 `services/rag/references/LightRAG` 和 `llama_index`，当前没有理由全库扫描它们。真正进入图谱切片再固定参考
  commit 和 golden cases。

### 4.3 上游事件与删除含义

已查清的上游语义记录在 04、14、15，不需要每个新会话从 Java 后端重查：

| 输入                   | 当前合同／语义                                                                                                                                                   |
|------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| DocumentReadyMessage   | 当前上游为 resourceId/version/content；RAG 已扩展接收未来可选 type/metadata，未改上游。旧事件使用默认链，未知类别写前失败；正文完整 Markdown，版本整数，不去空白 |
| ResourceDeletedMessage | topic `wisepen-resource-physical-destroy-topic`；typedResourceIds 按类型分组；所有资源类型均建立永久标记                                                         |
| ACL 重算               | payload resourceId；事件是刷新触发，读取上游当前资源 ACL，不把事件当权威 ACL 快照                                                                                |

上游三阶段区别：个人 `.Trash` 保留业务主档并剥离共享权限；softRemoveResources 移入 `RESOURCE_TRASH_COLLECTION`
；hardRemoveResources 清理回收集合后才发物理销毁事件。前两者不能调用永久 destroyed 标记。所有者仍可能通过本地
ACL，回收站搜索可见性是未实现的上游／读取 issue，不能宣称 ACL 已解决它。

上游内容事件须以 resourceId 作 Kafka key；内容与销毁不依赖跨 topic 到达顺序。重试、重复和销毁先到都由版本状态与永久标记处理。

### 4.4 ACL 与配置中容易误读的点

当前 ACL 从上游 `wisepen_resource_items`（代码中实际拼写）读 ownerId、updateTime、用户 action masks、computedGroupAcls；VIEW 为
bit 1。本地 ResourceAcl **实际已有 acl_revision**，来自 updateTime 的 UTC 毫秒时间戳，不是新增的上游完成版本、Fence 或内容
revision。旧计划中“不增加 ACL revision”应理解为不虚构新的跨服务一致性版本，不能据此删掉已有字段或另造保证。

VIEW 顺序与已有 ACL 测试已冻结：所有者、全局白名单、全局排除、群组规则；身份 scope 来自可信入口，公开业务参数不能让调用者自填身份或
ACL 覆盖字段。现有检索与读取经 SnapshotReader 消费它，不在 Storage 或 publication 内重复实现规则。

`container.py` 的旧错误 Mongo import 已修正，RAG 已声明 dependency-injector，uv.lock 已同步。`main.py` 通过 TaskGroup 管理
ACL，producer 与主 consumer 连接后再就绪，异常退出清理 Mongo／Nacos；健康检查未就绪或退出后为 503。

业务单元测试不需要启动 main；配置／Nacos 导入和注册属于运行时边界，`tests/application/test_lifespan.py`
已有正确替代边界的示例，保留真实生命周期与 Container。不要为了测试方便给生产代码加懒导入或隐式全局依赖。

### 4.5 图谱已经存在的事实与可复用源码

新 RAG 的 core/schema/graph.py 统一定义 GraphNode/GraphEdge/Ontology/EntitySpec/RelationSpec dataclass，GraphExtractor
与增量/补漏/离线图 repair 已实现；GraphStorage/GraphIndexer/Neo4j adapter 已实现；GraphRetriever.from_storage
种子组件已实现；授权图查询用例见 25，没有 PluginRegistry。图基础模型在较早切片已完成，不需要重新建立 Entity/Relationship
的第二套持久化事实模型：

| 当前模型  | 已有字段与身份语义                                                                                                                                                                                                                                                  |
|-----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| GraphNode | name、ontology_name、category、description、source_id。source_id 是**来源 Chunk ID**。id 是 ontology_name（非空时）+ category + 规范化名称；名称仅 strip/casefold/合并空白，ontology/category 不改写，不做实体链接。无 ontology 时 id 为 category:normalized_name。 |
| GraphEdge | source_id、target_id、relation_type、description、keywords、source_ids、metadata。source_id/target_id 是**端点 GraphNode.id**，source_ids 才是**来源 Chunk ID 列表**；id 为 source_id::relation_type::target_id，关系有方向。                                       |

这两个模型不携带 resource/revision/ACL 或 plugin_id。来源 Chunk 能提供其所属资源/revision；GraphSourceProjection 是按
Chunk 完整投影输入，Neo4j 共享图元与各来源关联分离，来源描述不合并，元数据保存资源/revision。不能把 Node 的 source_id 与
Edge 的 source_id 混用，也不能让最后一次 upsert 的单个来源取代所有来源。

旧 ontology 的精确参考文件是 `services/wisepen-rag-service/src/rag/application/plugins/core/ontology.py`
，本次已定向核对，无需全库重新寻找：

- Ontology：domain、description、entity_specs（dict）、relation_specs（dict）。
- EntitySpec：category、description、旧 node_type。
- RelationSpec：relation_type、description、allowed_sources、allowed_targets；端点限制列表为空时旧语义是不限制该侧类别。
- 旧 validate_node 检查类别存在，validate_edge 检查谓词、端点存在与允许类别；可提取这些规则。
- **不能原样导入旧模块。** 它依赖旧 GraphNodeKind、node_type 和 source_node_id/target_node_id，新模型没有这些字段；需要适配已有
  GraphNode/GraphEdge，并在计划中明确 domain→ontology_name 的映射。旧 plugin/core/registry.py 与 plugin.py
  只在实际需要插件消费者时定向参考，不把旧运行时一并迁入。

本片已定向核对 LightRAG commit 453dce83d6d0354a06e46c8d4029a0895c4e054b 的抽取与补漏，不依赖其 runtime；本片查询材料组织参考同一
commit，适配差异见 25；真实 Neo4j/图向量部署仍未验证。

## 5. 图事实、投影、种子召回与授权图用例已完成

### 5.1 当前实现与已确认合同

Ontology、GraphExtractor、增量复用、可选补漏及 scripts/repair_graph.py 已完成，详见 22 与 demo/graph.md。当前排期不做确定性
Producer，插件装配按实际消费者再确定。

- core.schema.graph 中 GraphNode/GraphEdge/Ontology/EntitySpec/RelationSpec 均为轻量 dataclass；仅模型输出 DTO 使用
  Pydantic 且字段都有 description。Ontology.from_file 支持 JSON/YAML/YML，内部可直接用 dataclass 构造，不建立配置 Registry。
- 原文是唯一抽取依据，section_path 只辅助消歧，不读取 contextual_context、邻居或全文。程序计算已有稳定 ID 并绑定当前 Chunk
  来源。
- Instructor 校验响应形状并使用自身重试；未知类别/关系、空白名称及非法端点按事实粒度丢弃。同块基础 ID 去重保留首次合法事实。
- 补漏可选、默认关闭、最多一次；提示词及合并都禁止修改首轮合法事实，无增量允许空返回，新关系可引用首轮实体。首轮超预算失败，补漏历史超预算记录并接受首轮结果。
- 成功替换 entities/relationships/graph_version，其余 metadata 保留。指纹覆盖策略、模型、tokenizer、完整本体、schema、生成配置和实际消息，不含并发数、来源
  ID 或无关 metadata。缓存整体合法且来源匹配才复用，空成功可复用。
- 所有块成功后才替换图 metadata；模型/解析异常取消并等待其他任务并传播，不执行事实 Indexer，数据库旧图事实保留。注册
  Pipeline 中的图阶段失败阻止本次 apply；独立 repair 的图失败不撤销已有 applied，也不回滚先前完成的事实/投影。默认 Pipeline
  不包含图阶段。
- repair 显式资源范围，只读当前 applied 未 destroyed Document/既有 Chunk，完整身份检查，写前复核完整发布状态，再保存全部块。force
  禁用缓存；资源错误继续其他资源，有错误非零退出，部分数据库写入保留。维护窗口同资源无其他 Chunk 写任务。
- repair 不重新分块、不 stage/apply、不重写普通向量、不加载 Nacos。不是生产在线重建或调度入口。

### 5.2 多来源投影与 repair

GraphIndexer 只读图事实，Ontology.parse_chunk_facts 统一解析，本体非法事实逐条丢弃；结构/来源损坏写前报错。一次解析后依次写拓扑与来源向量，没有独立向量
Indexer。Writer.replace_sources 接收完整 Chunk 来源投影（含空事实），仅替换给出的来源。Neo4j 默认每批 64
来源/显式事务，写入结果消费并等待提交；前置异常回滚当前批，前批保留。提交网络异常可能结果未知，传播后幂等重跑，不报告成功或加重试。schema
显式初始化，没有 ACL 投影、状态字段或跨库事务。

共享实体与有向关系图元只存身份，来源关联各自保存事实 JSON、平面 metadata；同 ID 可返回多个来源，描述不合并。一跳支持
outgoing/incoming/both、谓词和默认 100 逻辑关系预算，选中关系匹配来源完整返回。允许跨可见文档连接，应用层已通过同一
SnapshotReader 回查当前 Mongo 事实组装证据，见 25。

repair --with-graph 才构造 Neo4j
driver，事实保存后写全部图投影；抽取缓存不证明上次图写成功。事实成功、图失败保留事实，资源级继续并非零退出；重跑复用合法缓存并重写图。维护窗口同资源写任务串行，不回收未传入的历史
revision 或重分块旧 Chunk。

GraphRetrieval 已装配三种模式与 Common Pipeline，授权后再按逻辑种子组装一跳证据。GraphTraversal 接收逻辑实体 ID，只读一跳、不提供
depth；调用方看到关系提示和事实后选择下一跳。global 的端点描述只来自支持该关系的同一 Chunk，不额外收集其他端点来源。来源必须经
ResourceSnapshotReader 的 selected revision 与 ACL 回查；图 JSON/聚合描述不能替代授权或正文。mix 是否排期待定。

确定性 Producer 不排期，PluginRegistry 按实际消费者再确定。P8 首片已完成通用生产装配、消费者握手、资源物理 GC
与扩展包开发指南。父块、效果基线、在线重构建、非销毁历史贡献回收及 transport 留后；GC 只扫描永久 destroyed 资源，不能代替旧
revision 回收。

### 5.3 图向量与种子组件

GraphIndexer 显式注入两种 writer：拓扑 writer 可选，向量阶段需 embedding client、实体 writer、关系 writer 完整组；至少启用一种投影。默认来源批
64/embedding 批 32，当前批全部 embedding 成功后删旧写新，空事实清旧，两集合不补偿。Qdrant 图集合 dense-only，默认
graph_entity_source_vectors_v4/graph_relation_source_vectors_v4；text_sha256 记录实际索引文本；fact_sha256 与
graph_version 供不绑定本体的查询核对当前事实输入，均为普通向量 payload 字段。

Ontology.create_node/create_edge 绑定事实构造，parse_chunk_facts 为索引和缓存共享入口，strict=True 用于成功缓存的完整合法性。Node/Edge
不携带运行时本体对象，仍以 asdict 持久化普通字典。来源、第三方结构、当前本体与 ACL 是不同检查边界，不能用一次构造替代外部回查。

GraphRetriever.from_storage 固定 local/global/hybrid 种子配置，默认 hybrid 每路 30/总 60/Common RoundRobin，可注入 RRF。直接
embedding query 一次，两路并发 VectorReader；不持有 GraphReader 或 application 依赖。Candidate[GraphMatch] 包含种类和
VectorMatch，种类+来源记录为融合 ID，不合并不同来源。可直接接 Common RetrievalPipeline，prepare_candidates 授权后才扩展一跳。实际结果已为
GraphHit / GraphEvidence / GraphSourceChunk；不恢复旧 LocalGraphRetrieval/GraphContext。

repair --with-graph-vectors 独立选择向量组；--projection-only 至少启用一种投影，不创建抽取客户端、不写 Chunk，仍复核完整状态，向量阶段需要
embedding。RAG 不裁剪输出或管理结果缓存/懒读取，预算归 Chat。mix 排期待定。详情见 24 与 demo/graph.md。

### 5.4 本轮用户修正与实体关系提示

Ontology 只约束事实写入；GraphRetrieval、GraphTraversal 和 VectorRetrieval 均不注入 Ontology。core.schema.parse_graph_facts
负责结构/来源/合法端点解析，Ontology.parse_chunk_facts 只为写入与成功缓存添加本体规则。查询允许多个本体事实，不使用当前本体注册表筛掉历史合法事实，也不限制谓词必须属于某个查询本体。

Ontology.create_node 从类别和 RelationSpec 整理 GraphNode.relations，每项 GraphRelationOption 包含
relation_type/direction；空端点限制表示适用于该侧任意类别，两侧都允许时为 BOTH。提示随事实持久化，不表示当前可见关系存在。GraphNodeReference
只含 id/relations；GraphEvidence 的 GraphEntity 再含 source_id/description，移除重复的
name/ontology_name/category。模型选提示中的关系后，显式 traversal 查不到可见事实就返回空 relationships。

EvidenceBuilder / GraphEvidenceLoader 是公开共用类。删除重复的 filter 结构/类型、投影结构和单一来源检查，依赖底层
adapter；只保留业务范围约束、来源授权与事实过期核对。事实输入摘要不再依赖查询侧本体重渲染索引文本，旧向量缺少 fact_sha256
会被丢弃，可用 projection-only 重建。旧实体缺少关系提示时为 []；抽取策略版本已升级，正常 repair 会重算旧缓存以补齐提示，仅投影不能为旧
Mongo 事实补提示。

### 5.5 当前向量与检索合同

向量发布闭环与普通混合检索已按确认计划完成；VectorRecord/Query/Match 按用户最新要求并入 storage/vector.py，与端口共同归属向量存储；旧
schema 模块/导出移除，无兼容别名。其他已确认选择：

- 普通发布要求 dense 与 sparse 全部成功；sparse 固定服务端 qdrant/bm25 multilingual，client 使用
  cloud_inference=True，失败不降级 dense-only。
- 沿用 EMBEDDING_MODEL/DIMENSIONS 与旧连接配置键，集合独立 V4，默认 document_chunk_vectors_v4，named vectors 为
  dense/sparse。模型与维度目前显式注入，生产实际值仍待确认；embedding batch 默认 32，Qdrant upsert batch 默认 256，可注入。
- Adapter 内使用 uuid5 (NAMESPACE_URL, "wisepen-rag-v4:vector:" + record_id)，核心 Chunk ID 不变；payload 保存原 record_id
  与 metadata 中资源/revision，以及索引器显式提供的当前投影字段；不保存 text 或 ACL。图集合与扩展集合独立声明所需字段。
- VectorWriter upsert/delete 返回 None，无 Receipt；每批 wait=True 且 completed 才算成功，不做向量完整性回查。
- 同 revision 换分块/embedding 配置及旧投影删除仍是后续重建用例，本轮只保证当前配置下身份重放。
- VectorRetriever.retrieve (query: str) 返回 Candidate[VectorMatch]；不保留 VectorRetrievalQuery。from_storage 固定默认预算
  dense=30、sparse=30、融合上限=60，filters=None、include_embedding=False；默认显式 RoundRobin，可注入 RRF。FusionRetriever
  不增加调用时预算覆盖。
- VectorRetriever 初始化 mode 为 dense/sparse/hybrid（默认 hybrid）；sparse 只依赖 VectorReader、不需要 embedding 客户端，单路保留
  provider score，hybrid 才执行 Common 并发融合。单路只用 candidate_limit 作为仓储 top_k，拒绝
  dense_top_k/sparse_top_k；hybrid 两路未配置预算时各为 30，candidate_limit 限制融合输出。
- VectorRetrieval 绑定固定 VectorReader 与已配置的 EmbeddingClient，直接调用 from_storage 按本次 mode 选择
  dense/sparse/hybrid（默认 hybrid），构造 Retriever，最终 top_k 默认 5；focus 空白回退 query，diversity 为
  off/moderate/high。off 不取向量，MMR 使用已存 dense 向量，不对正文重新 embedding。
- Pipeline 的授权准备先于模型：批量 ResourceSnapshotReader.read_chunks，再核对向量 metadata 的 resource/revision 与实际
  Chunk。不可见、旧候选、错误身份和缺失事实省略，不补召回、不重查 ACL/publication。重排失败降级，其他阶段异常传播。
- ChunkHit 包含 chunk/resource/revision、section_id/section_path、完整 raw text、rank、score、seed_entities 与完整当前
  Chunk.metadata。metadata 来自授权回查后的 Mongo Chunk，不从向量 payload 拼装；没有白名单或查询侧业务
  schema。向量和内部准备对象不进入结果。排序后直接返回，命中后处理接口已移除；父块暂缓。
- 章节身份：Document 构造与 Chunker 共用 rag_section_id，使用旧服务 rsec_ + SHA-256 前 24 个十六进制字符规则；Document
  章节及父章节 ID、Chunk.section_id 和 ChunkHit 一致，Chunk.chunk_id 保持原格式。调用方只传 section_id，服务内部通过 Mongo
  章节索引定位，ResourceSnapshotReader 核对版本、授权与完整正文身份。DocumentReading 复用 OutlineFormatter，own/subtree
  完整切原文，无窗口/续读/递归深度。新摄入与测试采用新 ID，不做兼容或自动存量迁移。
- 生产配置值、Qdrant 服务端版本/BM25 支持与生命周期时序在装配切片再确认。

用户偏好：有真实矛盾或重要业务取舍直接指出并讨论，不在错误方案上补丁；不要擅自补一套“生产级”状态或策略。已确认的内部合同无需重复征求意见，普通命名、测试适配和可逆实现按当前风格推进。

### 5.6 平台、扩展与索引声明的最终约定

- 配置集中在 core/config/app_settings.py；服务生命周期在 main.py，沿用正常 Nacos 配置入口。不要恢复 models/app_settings 或
  runtime/main 拆分。默认 Container 可实例化和显式覆盖；业务组件通过注入使用平台依赖，不导入全局容器。
- 默认 Pipeline 为 MarkdownChunker → 可选 Contextualizer → ChunkFactsIndexer → 普通 VectorIndexer；ENABLE_CONTEXTUALIZE
  默认 False。GC 和 Qwen rerank 始终装配，没有 ENABLE_GC/ENABLE_RERANK。KAFKA_ENABLED 只控制 Kafka 消费；ENABLE_GRAPH_STORAGE
  默认 False。
- Document.type 精确匹配 pipeline_routes 普通字典，None 走默认链；未知 type 在 stage/事实写入前报错。所选完整链全部成功才
  apply，包含图 Indexer 时图失败也阻止发布。type/metadata/Pipeline 配置不进入 ContentRevision；影响摄入的业务字段变化要求上游提高
  version。
- extensions/_BUILDERS + AppSettings.EXTENSIONS 是显式源码登记机制。Extension 声明
  pipeline/vector_collections/api；register_extensions 注入共享路由、集合配置与 GC writer 列表，main 负责初始化与 API
  挂载。没有动态 tag 路由、在线注册入口、自动发现或独立插件 Runtime。
- VectorStorage 要求 collection 和 ensure_collection (metadata_index_fields: list[IndexableField])；参数必填，[]
  仍核对集合。IndexKind 为 KEYWORD/INTEGER/FLOAT/BOOLEAN，没有 DATETIME。数据库 SDK 类型只在后端转换。普通集合声明
  resource_id，图集合声明 resource_id/chunk_id/ontology_name，Employment 声明 resource_id/year/employee；content_revision
  均保留在 payload 核验。
- GraphRetrieval/GraphTraversal 可通过 filters 限定 resource_id/content_revision/ontology_name 的字符串 EQ、字符串列表 IN
  与 AND/OR/NOT；条件一致传给向量、拓扑和当前事实核对。GraphIndexer 来源 metadata 保存 ontology_name，Mongo
  回查以当前实体声明为准。无筛选时读取自由性保持，无关系的合法实体证据不丢弃。
- Employment 是公司内部合同示例：事件 metadata 仅 employee/start_date，Document 原样保存、Chunker 深拷贝；业务校验
  Transformer 不回写。year 只在专用向量投影中派生，factory 返回 employee/year 增量，资源身份由 VectorIndexer
  合并并保护。员工筛选按精确值，不恢复用户删掉的大小写/内部空白规范化函数。图是
  Person/Department/in_department，部门来自正文。工具只编排通用用例，没有专用 prepare、二次证据转换或额外 SnapshotReader 依赖。
- 来源属性和旧投影维护仍是显式维护行为。旧 Neo4j 来源缺 ontology_name 用 projection-only --with-graph
  重建；更改索引声明不自动物理删除既有字段索引，不自动重建已发布文档。GC 只清理永久 destroyed 资源，历史
  revision/重新分块残留另行处理。

## 6. 验证入口与交接要求

从仓库根目录运行：

```powershell
.venv/Scripts/python.exe -m pytest -c services/rag/pyproject.toml `
    --basetemp=D:/WisePenCloud-AI/wisepen_common/WisePenCloud-AI/.pytest_cache/rag-handoff `
    services/rag/tests -q
.venv/Scripts/ruff.exe check services/rag/src services/rag/tests services/rag/scripts
.venv/Scripts/python.exe -m compileall -q services/rag/src services/rag/tests services/rag/scripts
git diff --check
```

格式检查仅针对本轮修改 Python 文件，避免顺带改已提交的无关格式。测试优先保留现有行为覆盖，新增真正的边界、失败与 provider
合同用例，不写镜像实现的低价值检查。

根目录 workspace 包名仍有旧 rag-service，直接使用上面的现有 .venv 和 RAG pytest 配置更明确；不要为开始下一切片先重做依赖/入口装配。合并运行多个服务测试时必须明确
asyncio_mode，否则 RAG 的未加 mark 异步测试可能被错误当作普通同步测试。Windows 默认系统临时目录曾拒绝访问，所以这里把
pytest 临时文件固定在工作区 .pytest_cache 内，不把它当业务失败。

Common 回归入口为 tests/markdown、tests/ai_clients、tests/retrieval；Chat 参考入口为
services/wisepen-chat-service/tests/application/tools/output_cache。使用同一 .venv/python 并传
--asyncio-mode=auto。新增图谱切片首先跑自身定向测试，再跑 RAG 全服务和受影响的 Common/Chat 范围，不必为纯交接文档修改重跑这些业务测试。

| 要验证的问题                                            | 最短测试入口                                                                                   |
|---------------------------------------------------------|------------------------------------------------------------------------------------------------|
| 内容模型和完整摘要                                      | `tests/core/test_schema.py`、`test_content_revision.py`                                        |
| 原文与 Mongo 往返、分批／删除合同                       | `tests/backends/mongo/test_content.py`                                                         |
| 状态判断、CAS 与永久销毁                                | `tests/backends/mongo/test_publication.py`                                                     |
| 完整 publish 顺序及失败保留                             | `tests/application/publication/test_publish_flow.py`                                           |
| 上下文材料/预算/增量/并发与降级                         | `tests/ingestion/test_contextualizer.py`                                                       |
| 离线 repair 状态复核、全部向量重写与退出                | `tests/scripts/test_repair_context.py`                                                         |
| 精确内容回查                                            | `tests/application/publication/test_publish_facts.py`                                          |
| 请求内版本／权限决定、不可见缓存与请求隔离              | `tests/application/test_snapshot_selection.py`                                                 |
| Document／Chunk 授权读取、身份回查、缺失及异常合同      | `tests/application/test_snapshot_reading.py`                                                   |
| 向量 Retriever 配置、融合、响应校验、并发隔离           | `tests/retrievers/test_vector_retriever.py`                                                    |
| 通用正文三模式、授权、排序、metadata 与实体候选         | `tests/application/test_retrieval.py`                                                          |
| 全局目录、标题邻域、章节范围/身份/授权                  | `tests/application/test_structured_reading.py`                                                 |
| Mongo 批量章节定位、文档去重顺序与异常                  | `tests/backends/mongo/test_section_lookup.py`                                                  |
| 实际仓储到混合检索及章节读取的完整装配示例              | `tests/backends/qdrant/test_hybrid_composition.py`（显式调用 from_storage，off/high 两种组合） |
| Qdrant 按需向量与后端错误                               | `tests/backends/qdrant/test_vector.py`                                                         |
| 消费者、事件校验与确认时序                              | `tests/application/publication/test_consumer.py`                                               |
| 重试／延迟／DLQ／基础设施异常                           | `tests/backends/kafka/test_kafka_consumer.py`                                                  |
| ACL 规则、权威刷新及消费                                | `tests/acl`                                                                                    |
| Container/固定重排/本体工厂                             | `tests/test_container.py`                                                                      |
| 连接握手、清理及健康状态                                | `tests/application/test_lifespan.py`                                                           |
| GC 游标、重复删除、失败与迟到写入                       | `tests/application/test_gc.py`                                                                 |
| 类型路由、metadata/版本与注册链失败                     | `tests/application/publication/test_type_routing.py`                                           |
| 扩展包真实接口与文档示例                                | `tests/test_extensions.py`                                                                     |
| 公司内部合同、原样 metadata、员工/年份索引、部门/API/GC | `tests/application/test_employment_extension.py`                                               |
| 普通向量增量 factory 与身份保护                         | `tests/ingestion/test_vector_indexer.py`                                                       |
| 图三模式与本体筛选、授权及证据范围                      | `tests/application/graph/test_graph_retrieval.py`、`test_traversal.py`                         |

真实 Neo4j 用 `RAG_TEST_NEO4J_URI` 与对应 username/password/database，随机 graph_name 隔离标签和约束。真实 Mongo 用
`RAG_TEST_MONGODB_URI`，真实 Kafka 用 `RAG_TEST_KAFKA_BOOTSTRAP_SERVERS`，真实 Qdrant 用 `RAG_TEST_QDRANT_URL` 与可选
`RAG_TEST_QDRANT_API_KEY`。fixture 只创建和清理随机隔离数据库／topic／collection；未配置跳过，配置后连接失败报错。Kafka
集成仅缩短测试延迟，生产仍为 1 分钟／10 分钟。不要启动真实业务消费者或操作业务集合来替代这些隔离测试。

每一切片完成后同步 10／11 的相关现状和对应实施记录，说明实际验证、未验证外部行为和下一步；不以 fake
通过宣称真实部署完成，不把后续能力一起预建为空壳。

## 7. 下一会话：文档整合任务与开场指令

### 7.1 整合范围与产物

先核对 git status/HEAD，再以当前源码、测试和 29–31
的修正整理说明文档。主工作是去掉重复进度追加、消除旧合同、明确每份文档的用途；保持接口与已确认边界。发现源码与文档的真实矛盾时定向核对并记录，不据旧规划擅自实现新能力。

| 整合对象                                                                                                         | 应达到的结果                                                                                     |
|------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| [10 实施排期与验收门槛](10-实施排期与验收门槛.md)                                                                | 区分已完成、后续独立工作与待定项；历史测试数字链接到对应记录，避免把已完成能力仍写成下一片       |
| [11 设计文稿](11-RAG-V4设计文稿.md)                                                                              | 按最终模型、存储、Pipeline、发布/授权、图用例、生命周期与扩展合同组织，示例接口与源码一致        |
| [platform demo](../demo/platform.md)、[配置样例](../demo/platform-settings.yaml)                                 | 展示实际 Container/main、Nacos/配置、消费者握手、始终运行的 GC/rerank、扩展登记与关闭顺序        |
| [正文检索 demo](../demo/hybrid.md)、[图 demo](../demo/graph.md)、[上下文 demo](../demo/contextual.md)            | 类名、三模式、字段、集合初始化签名与 repair 参数一致；区分候选、授权证据、完整原文和模型内部材料 |
| [扩展指南](../src/rag/extensions/extensions.md)、[Employment README](../src/rag/extensions/employment/README.md) | 保留基础文件入库与高级专用索引/工具/API 的独立章节；当前公司内部合同是实际代码示例               |
| [本接手文档](16-session-handoff.md)                                                                              | 最后同步文档入口、最终基线、实际验证和后续边界，让新会话无需重读全部历史                         |
| 00–09 与 [架构图](架构图.md)                                                                                     | 定位为历史决策材料；标清与最终实现不同的地方，是否需要更新图由实际整合范围决定                   |

12–15、17–31 是历史实施记录。保留当时行为、测试数字与适配理由，必要时加后续修正链接；不要批量用最新结果覆盖历史验收，也不要未经要求删除或移动这些材料。旧
demo/extensions.md 已删除，指南位于 src/rag/extensions/extensions.md，旧 extensions/demo 已由 employment 取代。

### 7.2 最容易把旧方案写回来的地方

- HybridRetrieval 的当前公开名是 VectorRetrieval，无旧名别名；普通检索和 GraphRetrieval/GraphTraversal 都不注入
  Ontology。读取本体筛选使用可选 filters，不恢复本体 Registry 或从 ID 字符串解析类别。
- 没有独立 GraphVectorIndexer/graph_vector.py；GraphIndexer 统一写拓扑和来源向量。图与本体是 dataclass，向量值对象和索引声明在
  storage/vector.py，不在 core/schema/vector.py。
- 不恢复私有共用 EvidenceLoader、重复底层结构/过滤器校验或第二套权威事实。当前结果为
  GraphHit/GraphEvidence/GraphEntity/GraphSourceChunk 与 ChunkHit；实体候选为 id/relations，ChunkHit 返回完整当前
  metadata。
- GraphRetriever 只召回种子；授权和扩展归 Common Pipeline 的准备阶段。local 仅一跳，global 只带选中关系同来源端点，hybrid
  共享读取不能扩大单组范围。traversal 没有 depth；relations 是类别允许的提示，不承诺可见关系存在。RAG 不裁剪材料或管理结果缓存/懒读取。
- VectorStorage 初始化参数是必填 list[IndexableField]，不恢复 metadata_indexes 构造参数、字符串列表或无参接口。业务
  metadata_factory 是增量，不让扩展重复来源身份。payload 的 content_revision 用于核验，不等同于必须创建数据库字段索引。
- Employment 不再是多雇佣机构示例：没有 employer 或 metadata.employment，业务字段是平面 employee/start_date，部门关系来自正文，索引为
  resource_id/year/employee。Transformer 不再逐块 model_dump；工具不再重复加载和转换证据。
- 平台拥有唯一 publication/Kafka，垂类注册 Pipeline。完整注册链失败阻止 apply；独立 repair 部分成功保留，不能笼统写成“图失败不影响发布”。默认服务用
  Nacos，离线 repair 不加载 Nacos；这两个实际入口并存，不拆分正常生命周期。
- GC/rerank 始终运行，没有启用开关。GC 清理永久 destroyed 的全部已配置来源，保留标记，不负责活跃资源旧
  revision；不得将实现完成写成真实业务环境已验证。

### 7.3 文档验证与后续边界

纯文本整合检查本地链接、源码路径、旧名字、接口签名和 git diff --check。修改带可执行示例的指南/README 时运行
tests/test_extensions.py，涉及示例装配时再运行相应定向测试；只有改到代码或出现新疑点才扩展业务回归。最近完整验证仍以第 1 节和
31 的记录为准，不把旧测试结果写成本轮重新执行。

本次交接只更新本文并检查链接/差异，没有重新运行业务测试。文档整合也不启动 Nacos/业务消费者、连接业务数据库、执行
GC/repair、部署服务或创建新底层能力。真实后端/模型联调、效果基线、在线重构建、历史贡献回收、通用 HTTP/MCP transport 后续单独计划；mix
待定，确定性 Producer 不排期。

### 7.4 可以直接发给新会话的开场指令

> 请先读 services/rag/plans/16-session-handoff.md 第 0、1、5、6、7 节，以及 29–31 实施记录，核对 git status/HEAD。本次任务是
> RAG 文档整合：以当前源码和测试为准，整理 10/11、demo、扩展指南与 Employment README，最后更新接手入口。核心组件、Container/main、定时
> GC、type 路由、三模式正文/图检索与一跳 traversal 已实现，勿再次规划实现它们。当前 Employment 是公司内部合同，metadata 仅
> employee/start_date 原样保存与深拷贝，图为 Person/Department/in_department，VectorIndexer factory
> 合并业务增量和不可覆盖的来源身份；content_revision 保留核验但本示例不声明字段索引。查询不绑定 Ontology，图通过
> ontology_name 条件筛选，ChunkHit 返回完整 metadata，工具只编排通用用例。保留 12–15、17–31
> 历史记录及当时验证数字，清理当前说明中的旧合同；未决项保持未决，不引入新能力或连接业务环境。验证按改动范围执行并如实记录，保留两个既有子模块状态。
