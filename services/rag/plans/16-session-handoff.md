# RAG V4 会话接手文档

记录日期：2026-10-12。本文是新会话的首读入口，描述已完成实现、已查清事实、用户确认的设计约束和建议继续顺序。不是要求一次实现全部后续阶段。本轮向量更新见 [17 普通向量发布实施记录](17-vector-publication-implementation.md)：建议顺序第 1–4 步已实现，下一步是第 5 步生产装配。

## 1. 快速定位与当前基线

- 工作区：`D:\WisePenCloud-AI\wisepen_common\WisePenCloud-AI`，PowerShell。
- 当前分支：`dev/common`；本次交接前 HEAD 为 `de7bb34d`（`feat(rag-v4): 完成发布流程逻辑`），前一内容存储提交为 `5cb34e74`。后续会话先核对 HEAD，避免把记录中的提交位置当成永久状态。
- 新实现只在 `services/rag/src/rag`，测试在 `services/rag/tests`。旧 `services/wisepen-rag-service/src/rag` 与 `services/rag/references` 只作源码参考。
- 初次交接前 RAG 相关源码已提交；本轮新增向量模型、后端、Indexer 与测试，尚未提交。`WisePenView`、`services/rag/references/LightRAG` 有既存子模块状态变化，本轮没有处理；不要为清理工作区而恢复或提交它们。
- 最新服务测试：**274 passed、17 skipped**；服务 Ruff check 通过。17 个跳过项为 11 Mongo、3 Kafka、3 Qdrant 集成测试，均未配置测试连接，不能宣称真实后端已验证。向量轮次前基线为 202 passed、14 skipped。
- 前一实现轮次已通过修改文件 Ruff format、源码与测试 compileall、git diff --check，以及 core.schema 独立导入的依赖边界检查。

最短阅读顺序：本文 → [17 向量与发布实施记录](17-vector-publication-implementation.md) → `container.py`、`main.py`、`core/config/app_settings.py` → [15 Kafka 与发布实施记录](15-kafka-publication-implementation.md) 的生命周期要求。模型/存储合同见 11 相关章节与当前代码，无需先遍历整个仓库或 references。

文档与决策优先级：用户后续明确决策 > 已确认合同及当前实现 > 10／11 整合计划 > 00–09 历史背景。12–15、17 是实施记录，部分验收数字是阶段历史值。`架构图.md` 仍含 `document.py` 等旧目录和未来组件，不能据其判断当前文件存在。遇到真实合同冲突应向用户确认，不能自行把示例升级为已确认要求。

## 2. 已完成什么

| 能力 | 当前实际入口 | 完成范围 |
| --- | --- | --- |
| 统一内容身份与模型 | `core/schema/content.py` | ContentRevision、Document、DocumentStructure、Chunk；正文身份与 Common 结构模型 |
| 图基础模型 | `core/schema/graph.py` | GraphNode／GraphEdge；尚无图抽取、图存储或图查询实现 |
| ACL | `acl/models.py`、`acl/synchronizer.py`、`backends/mongo/acl.py` | 本地 VIEW 规则、权威资源刷新、Mongo ACL 投影及 Kafka 消费 |
| 摄入基础 | `ingestion/chunkers/markdown.py`、`ingestion/pipeline.py` | Common Markdown 适配、三个 Protocol、串行 fail-fast Pipeline |
| 内容事实存储 | `storage/content.py`、`backends/mongo/content.py` | Document／Chunk 完整对象读写、物理删除、显式唯一索引 |
| Chunk 事实 Indexer | `ingestion/indexers/chunk.py` | 仅调用 ContentWriter.upsert_chunks，保存最终 Chunk |
| 共享向量与过滤模型 | `core/schema/vector.py`、`filters.py` | VectorRecord/Query/Match，通用 metadata 条件与组合枚举 |
| 向量存储与 Qdrant | `storage/vector.py`、`backends/qdrant/` | collection-bound 双向量 upsert/query/delete，显式初始化，completed 确认；真实后端未验证 |
| 普通 VectorIndexer | `ingestion/indexers/vector.py` | retrieval_text → 分批 embedding → 双向量投影输入，只读 Chunk，全部成功才返回 |
| 发布完整用例 | `application/publication/publish.py` | 输入事件、消费者、publish 内部摄入与回查、条件发布、永久销毁 |
| 发布状态后端 | `backends/mongo/publication.py` | Mongo 条件 stage／apply、永久 destroyed 标记 |
| 通用 Kafka | `backends/kafka/consumer.py`、`retry.py` | 有限快速重试、延迟回投、DLQ、精确 offset 提交及资源清理 |
| 服务生命周期 | `container.py`、`main.py`、`api/router.py` | 目前装配 ACL；等待连接后就绪，消费异常撤销健康状态 |

当前**没有** Contextualizer、普通 Retriever、selected revision 读取用例、GraphStorage／GraphIndexer。VectorStorage、VectorIndexer、Qdrant adapter 与外部普通发布 Pipeline 已通过测试；真实 Qdrant 尚未验证。`ingestion/transformers/base.py` 只是接口，不能把空目录或 Protocol 计为增强能力已完成。

publication 消费者可通过显式依赖真实运行 Kafka；尚未注入全局 Container／main／Nacos。必需向量能力现已实现，但模型/连接配置、显式初始化与 publication 生命周期尚未装配。生产发布不能以 fake 或空必需投影替代。现有服务入口只运行 ACL 消费。物理销毁现在只撤销可见性并保留标记，尚未执行内容／向量／图物理清理。

## 3. 用户已确认的边界：不要重新设计

### 3.1 重构与模型

- 在新 `services/rag` 持续实现，不复制旧总 Container、Repository 层或 Snapshot 体系。没有线上兼容、双写、旧 schema 迁移或兼容别名要求。
- Document、DocumentStructure、Chunk 保持三个独立 dataclass；不要统一成 Node，也不要把 Chunk 移回 ingestion。
- 内容模型属于 `rag.core.schema`，不依赖 ingestion、storage、具体后端或 application。`Document` 正文字段实际叫 **`text`**。
- `ContentRevision` 是 frozen、slots dataclass；对原始 text 的 UTF-8 字节计算完整 SHA-256，不 trim、不规范化 Unicode。字符串身份为 `resource_id@document_version#完整摘要`。
- 内容身份不包含 metadata、Chunker 配置、embedding 模型或图谱配置。已 applied 的同身份提交直接跳过，不借重放更新 metadata；投影重建须另行定义用例。
- Chunk.raw_text 是正文事实；retrieval_text 由 `metadata["contextual_context"]` 与 raw_text 派生。增强不得改写正文、身份或来源。
- metadata 保持普通字典；不加 metadata schema、codec、注册表或 Stored 镜像模型。图模型写入 metadata 后持久化为字典，读回仍是普通 metadata。
- ACL 属于独立 ResourceAcl，不混入内容模型或发布状态。版本／冲突／销毁准入不是新增 ACL 鉴权。

### 3.2 Pipeline 与事实存储

- 内部 Pipeline 由开发者固定正确组合，**不增加组件识别、装配校验、依赖图或重复业务检查**。通过实际顺序和失败测试证明合同。
- `run(document)` 返回所有 Indexer 成功后的最终同一 `list[Chunk]`，不复制、不包装。Transformer 可原地增强或返回新列表；后续使用其返回值。Indexer 只读输入。
- 普通配置固定 `Chunker -> Transformers -> ChunkFactsIndexer -> 全部必需投影 Indexers`。
- 必需 Indexer 只有确认全部所需写入成功才能返回。发布依赖这一成功合同，不新增向量完整性查询、完成清单、Receipt 或投影状态字段来证明完成。
- 任一阶段失败停止后续阶段，已完成写入保留；不回滚、不并行 Indexer、不加 continue_on_error。传输重试属于 Kafka，不放入 Pipeline。
- ContentStorage 只读写事实，不计算 revision、不推断 active、不管理 publication 或 ACL。写入成功返回 None，异常直接传播。
- ContentStorage 批量读取去重保序、缺失省略；revision Chunk 按 chunk_index 升序；空批量无 I/O。Document 按 `(resource_id, content_revision)`、Chunk 按 chunk_id 完整替换，每批 500 条有序 ReplaceOne。
- upsert 不删除批次外 Chunk，metadata 不合并。当前不支持同 revision 整版重新分块替换；更改分块配置后的旧 Chunk 清理不能擅自塞进 upsert。
- Mongo 显式注入异步 collection，显式 ensure_indexes；不在每次写入建索引，不加 Repository／Storage Router 或全局依赖。BSON 不支持值正常报错，部分成功不补偿。

### 3.3 publication 当前形态

publication 是 application 用例，没有独立于摄入的领域能力。目录已收敛为 **models.py、store.py、publish.py**，另有仅做导出的 `__init__.py`。

`publish.py` 同时定义 DocumentReadyEvent、ResourceDestroyedEvent、PublicationEventConsumer 和 DocumentPublication。消费者只注入一个 publisher 业务对象，内容直接调用 `publish(document)`，销毁直接调用 `destroy_resources(ids)`。

独立 DocumentIngestion 和原 consumer.py／ingestion.py／publisher.py 已移除。发布器不再公开 stage_revision／apply_revision；Store 仍有这些原子状态操作。不要为了恢复之前的分层重新加回这些类、文件或兼容导出。

发布器显式注入 `store: PublicationStore`、`reader: ContentReader`、`writer: ContentWriter`、外部完整 `pipeline: IngestionPipeline`。内容读写可由同一个 MongoContentStorage 同时实现。

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

stage：先 destroyed，再低于 staged/applied 最高版本，再最高版本同版本不同摘要冲突，再已 applied 相同身份跳过；同身份 staged 重放允许重建。更高版本替换 staged，旧 applied 保留。

发布前必须读到正确 Document；资源、版本、完整正文摘要和 revision 字符串一致。该 revision 的 Chunk ID 排序列表与本次成功 Pipeline 返回列表完全一致，缺失、多余或乱序均拒绝发布。允许零 Chunk。Common 当前会把只有标题的文档生成一个 flat Chunk，不强加标题文档必须零 Chunk。

调用方保证同资源构建串行，当前通过主 Kafka 分区消费执行。Mongo 条件更新仍防止新候选或销毁使旧任务错误发布。CAS 失效重新读取只做分类；仍无法完成时抛 PublicationStateChangedError，不在 Store 自动循环重试。不加锁、租约、完成清单、时间戳或跨库事务。

destroy_resources 永久置 destroyed=True，清空 staged/applied；未摄入资源也建标记，每批 500 个有序 upsert。标记不删除、不恢复；正在运行的任务可能留下事实或投影，但不能恢复发布。物理清理由后续用例完成。

### 3.4 Kafka 已定策略

- **不使用或改造 common.kafka**；先前尝试已复原。ACL 和 publication 都使用 `rag.backends.kafka`，后端不导入业务模块。
- 默认快速重试 3 次，等待 1／2／4 秒；随后 `<main>-retry-1m`、`<main>-retry-10m` 各一次；仍失败进入 `<main>.DLT`。正常链最多 5 次重试、6 次业务执行。
- 坏 JSON／字段、身份不符、同版本冲突直接 DLQ；暂时事实不完整与 I/O 走重试。应用拥有异常分类。
- 延迟 topic 不执行业务：等待到期后把原 value、key、headers 回投原主 topic 的原分区。新版本可以先于旧重试发布，旧重试由版本规则淘汰。
- relay 独立消费，不阻塞主 topic；retry 队列自身逐条等待，不是按资源并行调度器，也不承诺精确到期执行。
- 成功或明确跳过后，提交当前分区 offset+1。转发 retry／DLQ 先确认发送再提交输入位置，发送或提交失败停止循环、异常交由入口重启。基础设施故障不向同一个故障 broker 递归转发。
- 保留原始消息、key、上游 headers；保留的 rag- headers 携带原 topic／partition／offset、retry-count、available-at、异常类型／消息／traceback。DLQ 不自动消费，没有自动重放入口。
- producer 用 acks=all，发送确认及请求预算默认 30 秒。未启用 producer idempotence，因为 aiokafka 在该模式下不会按普通请求超时过期批次；当前采用有界普通发送及应用幂等。
- 语义为 at-least-once，发送与 commit 无 Kafka 事务；崩溃、提交失败或发送超时后仍送达都可能重复。全局业务执行次数不能用正常链预算承诺 exactly-once。
- 入口必须 await run 并观察异常。主 poll 预算覆盖业务与快速重试；relay 至少覆盖延迟+30 秒。topic 创建、权限、保留期和重启由部署配置负责，库不管理业务 topic。

## 4. 已查清的实现与上游事实

### 4.1 内容与索引位置

当前 Mongo 内容操作已经收敛在 `backends/mongo/content.py`，**不存在必须继续修改的 chunk／document 子包**。13 记录已按现状修正；早期用户方案指定的路径不要当成当前目录树。集合绑定仍为 `document_revisions` 与 `doc_chunks`，发布状态为 `resource_publications`，本地 ACL 为 `resource_acls`。

内容唯一索引为 Document `(resource_id, content_revision)`、Chunk `chunk_id`、Chunk `(resource_id, content_revision, chunk_index)`；发布状态 `resource_id` 唯一。TypeAdapter 直接处理原模型与嵌套 ContentRevision。retrieval_text 不持久化，不恢复旧 spans／node_ids。

### 4.2 Common 与旧代码复用点

- Markdown：`services/wisepen-common/src/common/utils/markdown`。RAG 只适配字段与资源/revision 内 ID，不重写解析或分块算法。事件 to_document 通过 Common 完整分块结果提取 sections/anchors，Pipeline 再正式分块；当前存在这次重复解析，已知且未另造解析接口。
- Embedding：`services/wisepen-common/src/common/utils/ai_clients/embedding.py` 的 EmbeddingClient 已有 `embed(model=..., texts=list[str], dimensions=...) -> list[list[float]]`，按 response.index 保序并核对数量。它拒绝空 texts，所以 VectorIndexer 的零 Chunk 成功路径必须在调用前结束。
- 检索：`services/wisepen-common/src/common/utils/retrieval` 已导出 Candidate、Retriever、FusionRetriever、RoundRobinFusion、RrfFusion、RetrievalPipeline、RankedCandidate、reranker／diversity。
- Common BM25Retriever 为传入的内存 Candidate 列表建索引，**不能当作 Qdrant sparse adapter**。Dense／sparse 的 provider 召回适配留在 RAG。
- Common RetrievalPipeline 在 reranker 失败时保留原召回顺序，计划已接受此降级行为；需要严格失败合同的用例应明确讨论，不能默认为与 ingestion 同样 fail-fast。
- 旧向量源码优先看 `services/wisepen-rag-service/src/rag/core/persistence/qdrant/document_vector_repository.py`、`graph_vector_repository.py`，旧普通索引编排在 `application/document/indexing.py`。只提取 provider 行为、算法或用例，不复用旧 repository、发布完整性 API、payload 或运行时。
- 图谱后续参考 `services/rag/references/LightRAG` 和 `llama_index`，当前没有理由全库扫描它们。真正进入图谱切片再固定参考 commit 和 golden cases。

### 4.3 上游事件与删除含义

已查清的上游语义记录在 04、14、15，不需要每个新会话从 Java 后端重查：

| 输入 | 当前合同／语义 |
| --- | --- |
| DocumentReadyMessage | topic `wisepen-document-ready-topic`；JSON 字段 resourceId、version、content；正文完整 Markdown，版本为整数，不去正文空白 |
| ResourceDeletedMessage | topic `wisepen-resource-physical-destroy-topic`；typedResourceIds 按类型分组；所有资源类型均建立永久标记 |
| ACL 重算 | payload resourceId；事件是刷新触发，读取上游当前资源 ACL，不把事件当权威 ACL 快照 |

上游三阶段区别：个人 `.Trash` 保留业务主档并剥离共享权限；softRemoveResources 移入 `RESOURCE_TRASH_COLLECTION`；hardRemoveResources 清理回收集合后才发物理销毁事件。前两者不能调用永久 destroyed 标记。所有者仍可能通过本地 ACL，回收站搜索可见性是未实现的上游／读取 issue，不能宣称 ACL 已解决它。

上游内容事件须以 resourceId 作 Kafka key；内容与销毁不依赖跨 topic 到达顺序。重试、重复和销毁先到都由版本状态与永久标记处理。

### 4.4 ACL 与配置中容易误读的点

当前 ACL 从上游 `wispen_resource_items`（代码中实际拼写）读 ownerId、updateTime、用户 action masks、computedGroupAcls；VIEW 为 bit 1。本地 ResourceAcl **实际已有 acl_revision**，来自 updateTime 的 UTC 毫秒时间戳，不是新增的上游完成版本、Fence 或内容 revision。旧计划中“不增加 ACL revision”应理解为不虚构新的跨服务一致性版本，不能据此删掉已有字段或另造保证。

VIEW 顺序与已有 ACL 测试已冻结：所有者、全局白名单、全局排除、群组规则；身份 scope 来自可信入口，公开业务参数不能让调用者自填身份或 ACL 覆盖字段。未来检索与读取消费它，不在 Storage 或 publication 内重复实现规则。

`container.py` 的旧错误 Mongo import 已修正，RAG 已声明 dependency-injector，uv.lock 已同步。`main.py` 通过 TaskGroup 管理 ACL，producer 与主 consumer 连接后再就绪，异常退出清理 Mongo／Nacos；健康检查未就绪或退出后为 503。

业务单元测试不需要启动 main；配置／Nacos 导入和注册属于运行时边界，`tests/application/test_lifespan.py` 已有正确替代边界的示例，保留真实生命周期与 Container。不要为了测试方便给生产代码加懒导入或隐式全局依赖。

## 5. 建议下一步开发顺序

现有 10 的 P0–P8 是大阶段规划，完成情况不能简单按编号连续推断：内容事实、publication、Kafka 与普通向量投影已落地，生产 publication 装配、读取和图谱仍待实现。当前优先完成真实后端验证与生产装配。

| 顺序 | 切片与范围 | 完成标准 |
| --- | --- | --- |
| 1（已实现） | VectorReader／Writer／Storage、共享向量模型与过滤合同 | 模型归 core/schema；collection 绑定实例，无 namespace/Receipt；导入边界与读写消费者测试通过 |
| 2（已实现） | Qdrant collection-bound 后端及显式初始化 | upsert/query/delete、过滤翻译、completed 确认通过；3 个隔离集成测试未配置而跳过 |
| 3（已实现） | 普通 Chunk VectorIndexer | retrieval_text → 分批 embedding → VectorWriter；只读输入、零 I/O、失败保留与响应校验通过 |
| 4（已实现） | 外部完整普通 Pipeline 与发布闭环 | 事实在向量前；向量失败不 apply，成功才 apply；重放／高版本／销毁通过。测试替换驱动 I/O，真实 Qdrant 未验证 |
| 5 | 完整生产装配与 publication 运行入口 | 确认配置、索引初始化、必需投影和消费者生命周期后接 Container／main；不能先上线只有 Chunk facts 的“原文落库即发布” |
| 6 | selected revision 读取边界、Dense／sparse hybrid 与直接内容读取 | 请求首次选择 applied 后固定该 revision；本地 ACL 验证，Mongo 正文回查；授权正文才进 rerank／context；旧候选不混入新 revision |
| 7 | Contextualizer 与普通内容能力完善 | 同 resource/revision/section 连续上下文，预算明确，只增强 metadata；接在事实 Indexer 前；图谱不可用时普通检索仍独立工作 |
| 8 | 物理清理、按事实重建投影及回收站可见性 | 先永久撤销发布，再清内容与各投影；不删销毁标记；重复／部分失败可继续；回收站 issue 需独立明确上游合同 |
| 9 | 可选图谱生产与读取 | Ontology／Plugin → GraphExtractor → Chunk metadata 事实 → GraphIndexer／GraphStorage → LightRAG local/global/hybrid → seed／有界遍历 |

第 1–4 步已由本轮完成，下一轮优先第 5 步生产装配，并配置隔离测试验证真实 Qdrant。第 6–7 步属于 P4 普通检索与增强；原大计划先排 Contextualizer，实际是否提前由下一轮目标决定，**基础向量发布可以直接使用 raw_text 派生的 retrieval_text，不以 contextual LLM 为前置条件**。可选图谱始终在普通发布成功后独立构建，失败不撤销普通内容。

### 本轮已确认的向量选择与下一轮待确认项

用户确认本轮执行范围到向量发布闭环；共享 VectorRecord/Query/Match 提升到 core/schema/vector.py，Storage 只保留端口。其他已确认选择：

- 普通发布要求 dense 与 sparse 全部成功；sparse 固定服务端 qdrant/bm25 multilingual，client 使用 cloud_inference=True，失败不降级 dense-only。
- 沿用 EMBEDDING_MODEL/DIMENSIONS 与旧连接配置键，集合独立 V4，后续默认 document_chunk_vectors_v4，named vectors 为 dense/sparse。模型与维度目前显式注入，生产实际值仍待确认；embedding batch 默认 32，Qdrant upsert batch 默认 256，可注入。
- Adapter 内使用 uuid5(NAMESPACE_URL, "wisepen-rag-v4:vector:" + record_id)，核心 Chunk ID 不变；payload 保存原 record_id 与 metadata 中资源/revision，不保存 text、ACL 或未来图字段。
- VectorWriter upsert/delete 返回 None，无 Receipt；每批 wait=True 且 completed 才算成功，不做向量完整性回查。
- 同 revision 换分块/embedding 配置及旧投影删除仍是后续重建用例，本轮只保证当前配置下身份重放。
- 下一轮确认生产配置值、Qdrant 服务端版本/BM25 支持、启动初始化/清理和健康状态时序；不重新询问上述已冻结合同。

用户偏好：细节拿不准要问，尤其上述业务／部署取舍；不要擅自补一套“生产级”状态或策略。已确认的内部合同无需重复征求意见，普通命名、测试适配和可逆实现按当前风格推进。

## 6. 验证入口与交接要求

从仓库根目录运行：

```powershell
uv run --project services/rag pytest -q services/rag/tests
uv run --project services/rag ruff check services/rag/src/rag services/rag/tests
uv run --project services/rag python -m compileall -q services/rag/src/rag services/rag/tests
git diff --check
```

格式检查仅针对本轮修改 Python 文件，避免顺带改已提交的无关格式。测试优先保留现有行为覆盖，新增真正的边界、失败与 provider 合同用例，不写镜像实现的低价值检查。

| 要验证的问题 | 最短测试入口 |
| --- | --- |
| 内容模型和完整摘要 | `tests/core/test_schema.py`、`test_content_revision.py` |
| 原文与 Mongo 往返、分批／删除合同 | `tests/backends/mongo/test_content.py` |
| 状态判断、CAS 与永久销毁 | `tests/backends/mongo/test_publication.py` |
| 完整 publish 顺序及失败保留 | `tests/application/publication/test_publish_flow.py` |
| 精确内容回查 | `tests/application/publication/test_publish_facts.py` |
| 消费者、事件校验与确认时序 | `tests/application/publication/test_consumer.py` |
| 重试／延迟／DLQ／基础设施异常 | `tests/backends/kafka/test_kafka_consumer.py` |
| ACL 规则、权威刷新及消费 | `tests/acl` |
| 连接握手、清理及健康状态 | `tests/application/test_lifespan.py` |

真实 Mongo 用 `RAG_TEST_MONGODB_URI`，真实 Kafka 用 `RAG_TEST_KAFKA_BOOTSTRAP_SERVERS`，真实 Qdrant 用 `RAG_TEST_QDRANT_URL` 与可选 `RAG_TEST_QDRANT_API_KEY`。fixture 只创建和清理随机隔离数据库／topic／collection；未配置跳过，配置后连接失败报错。Kafka 集成仅缩短测试延迟，生产仍为 1 分钟／10 分钟。不要启动真实业务消费者或操作业务集合来替代这些隔离测试。

每一切片完成后同步 10／11 的相关现状和对应实施记录，说明实际验证、未验证外部行为和下一步；不以 fake 通过宣称真实部署完成，不把后续能力一起预建为空壳。

## 7. 可以直接发给新会话的开场指令

> 请先读 `services/rag/plans/16-session-handoff.md` 与 17 向量实施记录。当前统一内容模型、内容存储、publication/Mongo 状态、RAG 独立 Kafka、共享向量/过滤模型、Qdrant 后端和 VectorIndexer 已完成，最新测试 274 passed、17 skipped；真实 Mongo/Kafka/Qdrant 未配置。普通发布要求 dense 与服务端 BM25 sparse 全部写入 completed，Pipeline 外部装配、事实 Indexer 在向量前，不做组件校验或向量回查。下一轮先规划生产配置、显式初始化与 Container/main publication 装配，按交接文档对未确认运行细节提窄问题，不重新设计已冻结合同。代码继续写在 services/rag，不改 common.kafka、不加旧 API 兼容。
