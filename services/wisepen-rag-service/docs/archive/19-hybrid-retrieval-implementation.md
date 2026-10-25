# 19：泛型检索 Pipeline、VectorRetriever 与混合检索实施记录

记录日期：2026-10-12。实现基于用户最终确认的配置式 Retriever 计划。该切片前基线为 Common 检索 11 passed、Chat 缓存 BM25 工具
3 passed、RAG 322 passed／17 skipped。

## 1. 已完成边界

| 层次                   | 当前合同                                                                                                                                  |
|------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| Common                 | Candidate[T] 只承载 candidate_id、payload、rank、score；统一排名载体，没有固定正文、向量或 metadata 字段，没有 RankedCandidate            |
| VectorRetriever        | from_storage 固定依赖与预算、过滤、向量返回配置；retrieve(query: str) 返回 Candidate[VectorMatch]，只调用 VectorReader 与 EmbeddingClient |
| ResourceSnapshotReader | 已有的请求内版本与资源授权规则；本轮不修改其生命周期或 ContentReader 合同                                                                 |
| HybridRetrieval        | 每调用直接 from_storage 装配 Retriever/Pipeline，在 prepare_candidates 中授权回查并核对投影身份，然后排序与组装 ChunkHit                  |
| Qdrant                 | 按需返回命中已存 dense 向量，默认不取；不选择版本、不判权、不重新 embedding 正文                                                          |
| Chat 缓存搜索          | 类型化 payload 保存 content_id 与 MarkdownChunk，以提取函数建 BM25 索引；工具结果和定位合同保持                                           |

Common 原有的并发组合、基础展平、RoundRobin、RRF 去重及稳定排序、嵌套 FusionRetriever 保留。预算在初始化时确定，不增加调用时覆盖，也不引入
QueryT、查询文本提取函数或 VectorRetrievalQuery。业务 payload 不被 Common 改写，阶段分数始终属于 Candidate.score。

## 2. 调用与装配

VectorRetriever.from_storage 显式接收 reader 与已绑定 model/dimensions 的 embedding_client；检索配置默认
dense_top_k=30、sparse_top_k=30、candidate_limit=60、filters=None、include_embedding=False。融合默认显式 RoundRobinFusion，可注入
RrfFusion 等策略。

初始化 mode 支持 dense/sparse/hybrid，默认 hybrid；dense/hybrid 需要 embedding 客户端，sparse 只需 VectorReader。仅 hybrid
装配 Common FusionRetriever 并发融合；单路保留 provider 排名/分数，candidate_limit 对所有模式生效，单路仅使用
candidate_limit 作为仓储预算，并拒绝 hybrid 专属 dense_top_k/sparse_top_k
参数。模式参考 [LlamaIndex VectorIndexRetriever](https://developers.llamaindex.ai/python/framework-api-reference/retrievers/vector/)
，不引入其完整模式集合或仓储合同。

各路线按 provider 返回顺序构造 Candidate，初始 score 是 provider score；融合更新 Candidate，原 VectorMatch.score
保留。RoundRobin 阶段分数仍为 0，RRF 写倒数排名累计分数。Dense 为查询生成一次 embedding，并沿用 VectorIndexer
的数量、维度、有限数值及响应错误合同；Sparse 直接发出文本查询。Hybrid 两路失败不降级为单路。

HybridRetrieval 直接绑定固定的 VectorReader、已配置的 EmbeddingClient 和可选融合策略。每次调用在用例内部直接执行
VectorRetriever.from_storage，显式指定 hybrid 并传入本次预算、过滤和向量返回选项，无独立工厂合同。已连接的仓储与客户端共享，组件组装不新建连接：

```python
usecase = HybridRetrieval(
    snapshot_reader=snapshot_reader,
    vector_reader=vector_reader,
    embedding_client=embedding_client,
)
snapshot = ResourceSnapshot(trusted_scope)
hits = await usecase.retrieve("查询文本", snapshot=snapshot)
```

可选 ModelReranker 使用 text_getter=lambda payload: payload.chunk.raw_text。用例默认 top_k=5、focus="
"、diversity="off"，召回预算和过滤仍由每次调用显式指定。空白 focus 回退 query；moderate/high 使用 Common MMR 的 0.75/0.5
参数，并在构造 Retriever 时设置 include_embedding=True。off 不请求候选向量。

每次调用只保存局部 Retriever、Pipeline 和绑定 snapshot 的准备回调；共享 usecase 只保存固定依赖、不更新共享组件配置。独立能力调用创建新
ResourceSnapshot，同一次上层编排显式共享。快照只固定资源版本与可见性，不固定每次召回的排名分数。

## 3. 授权、结果与失败

prepare_candidates 先省略缺少有效资源/revision 定位的投影，再批量 read_chunks。实际 Chunk 必须通过资源级 ACL、selected
revision，以及向量 metadata 的 resource_id/content_revision 与正文事实的身份核对。缺失、不可见、旧版本或错误投影身份省略，不从正文反向修补
metadata，不切换版本或补召回。

授权后的 PreparedChunk 仅包含 Chunk 与可选 embedding；原融合顺序和分数保留，rank 连续编号。未授权、旧版本及错误投影正文不会进入外部重排模型。Snapshot
首次不可见决定也复用，调用期间 publication/ACL/destroyed 变化不触发重查。

ChunkHit 仅包含 chunk_id、resource_id、content_revision、section_id、section_path、text（raw_text）、rank、score。score
是最终排序阶段分数，可来自融合、重排或 MMR；内部 metadata 与向量不进入结果。

Pipeline 召回或准备为空时跳过排序。只有重排失败记录日志并保留重排前顺序与分数；召回、准备与多样化异常直接传播。排序、top_k
和组装后直接返回应用命中；可选后处理接口已在后续 20 切片按用户确认完整移除。

Qdrant 的 VectorQuery.include_embedding 默认 False；开启时 with_vectors 只请求配置的 dense 名称，dense/sparse 两路都返回
VectorMatch.embedding。请求后缺失、维度错误、非法类型或非有限数值作为后端数据错误传播。写入、collection 初始化和 publication
合同没有改变。

## 4. 实际验证

| 检查                                                     | 结果                       |
|----------------------------------------------------------|----------------------------|
| Common 检索全套                                          | **24 passed**              |
| Chat 缓存 BM25 工具定向                                  | **3 passed**               |
| Common AI 客户端与检索联合回归                           | **50 passed**              |
| RAG Retriever、application、VectorIndexer 与仓储装配定向 | **80 passed**              |
| 向量存储/共享模型/VectorIndexer 定向（检索切片首次验收） | **87 passed、3 skipped**   |
| RAG 全服务（含装配示例）                                 | **411 passed、17 skipped** |
| RAG 全服务与修改范围 Ruff check                          | 通过                       |
| 修改范围 Ruff format --check                             | 通过                       |
| RAG 源码/测试、Common 检索与 Chat 工具 compileall        | 通过                       |
| git diff --check 与新文件空白检查                        | 通过                       |

从仓库根目录运行 Common 检索：

```powershell
uv run --project services/rag pytest services/wisepen-common/tests/ai_clients services/wisepen-common/tests/retrieval -q
```

在 services/rag 内运行：

```powershell
uv run pytest tests/retrieval tests/application/test_retrieval.py tests/backends/qdrant/test_hybrid_composition.py tests/ingestion/test_vector_indexer.py -q
uv run pytest tests/backends/qdrant tests/core/test_vector_schema.py tests/ingestion/test_vector_indexer.py -q
uv run pytest -q
uv run ruff check src/rag tests
```

在 services/wisepen-chat-service 内运行：

```powershell
uv run --with pytest --with pytest-asyncio pytest tests/application/tools/output_cache/test_search_by_bm25.py -q
```

新增测试覆盖准备阶段先于模型、准备失败和空结果跳过模型、payload 透传、预算与提取函数、provider/阶段分数分离、两路并发、查询隔离；Qdrant
按需向量与坏数据；application 授权/版本/投影身份、快照复用与新快照、部分结果、排序/top_k/focus/MMR、每调用配置、融合上限及异常传播（后处理相关测试已在
20 切片移除）。真实 Qdrant 既有集成测试已增加两路向量返回断言，但未执行到真实后端。

17 个跳过项为 11 Mongo、3 Kafka、3 Qdrant 集成测试，未配置隔离端点；不据此宣称真实后端、模型服务或生产部署完成。

## 5. 可运行的实际装配示例

直观的装配说明见 [demo/hybrid.md](../demo/hybrid.md)，按底层依赖、SnapshotReader、HybridRetrieval 的直接 from_storage
装配与请求调用展开，包含 Python 代码块；阅读无需启动后端。

用户要求直接查看 from_storage 的使用位置后，新增 [完整装配测试](../tests/backends/qdrant/test_hybrid_composition.py)
。示例使用真实
QdrantVectorStorage、MongoContentStorage、MongoPublicationStore、ResourceSnapshotReader、VectorRetriever、Common Pipeline 和
ModelReranker；使用真实 Common EmbeddingClient；数据库 collection、Qdrant 网络请求、ACL 读取和模型响应使用显式测试替身，不加载生产运行配置。

该示例将已连接的向量仓储、embedding 客户端和模型配置直接传给 HybridRetrieval；model/dimensions 只在 EmbeddingClient
初始化时配置。HybridRetrieval.retrieve 内直接调用 VectorRetriever.from_storage，追加本次预算、filters、include_embedding，得到配置固定的
Retriever，再交给 Pipeline 执行 retrieve (query)。snapshot 只传给 application 准备阶段。
off/high 两种实际组合均通过，覆盖 dense=7、sparse=9 的仓储查询参数、按需向量返回、一次查询 embedding、资源授权正文回查、模型输入
raw_text 和最终命中分数。生产 Container/main 检索入口仍未装配。

在 services/rag 内运行：

```powershell
uv run pytest tests/backends/qdrant/test_hybrid_composition.py -v
```

结果为 **2 passed**；同步客户端初始化配置与三种召回模式后 RAG 全服务 **411 passed／17 skipped**。新文件 Ruff check、格式检查和
compileall 通过。

## 6. 客户端配置收敛

根据后续确认，EmbeddingClient 在初始化时固定 model、可选 dimensions，embed 只接收 texts；InstructionClient 初始化固定
model，complete/structured_complete 不再接收模型覆盖。VectorIndexer、VectorRetriever.from_storage 和 HybridRetrieval 移除重复
model/dimensions 参数，响应数量/维度/有限数值检查及 ValueError → RuntimeError 失败分类保留。dimensions 为 None
时使用供应商默认维度，不向供应商发送该参数；仓储仍核对 collection 维度。无旧调用参数兼容层。

模式验收补充：dense/sparse 只调用所选路线并保留 provider score；sparse 无 embedding 依赖；错误 mode 或 dense/hybrid 缺失客户端在
I/O 前失败；三种模式各自支持同一实例并发查询隔离。

## 7. 后续边界

- 父块暂缓，命中后处理接口已移除，不增加 ParentBuilder 或空 parents 字段。
- 标题树与章节读取已在 [20 实施记录](20-structured-reading-implementation.md) 落地；下一切片为
  Contextualizer，图谱生产及图检索按排期推进；graph 抽象在真实图用例需求确定后定义。
- 普通混合检索独立于图谱，当前不增加资源范围参数。
- 生产模型/连接配置、显式初始化和 Container/main 生命周期装配继续留后。
- 已同步 04、10、11、16；18 保留资源快照切片的历史验收结果。本轮改动未提交或推送，既有子模块状态未处理。
