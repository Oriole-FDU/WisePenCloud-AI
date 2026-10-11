# 普通混合检索装配示例

这个示例展示实际组件之间如何连接，重点是 `VectorRetriever.from_storage` 的调用位置，以及它与 application 用例的关系。代码按阅读顺序分段，连接、凭证和模型配置由外部提供，可以将这些段落放进自己的装配入口；这里省略启动、关闭连接与 API 入口。

## 1. 准备固定依赖

先构造客户端和仓储。这些对象可以由多个调用共享。

```python
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from qdrant_client import AsyncQdrantClient

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.ai_clients.rerank import RerankClient
from rag.backends.mongo.acl import MongoResourceAclRepository
from rag.backends.mongo.content import MongoContentStorage
from rag.backends.mongo.publication import MongoPublicationStore
from rag.backends.qdrant.vector import QdrantVectorStorage

# 大写变量代表装配入口提供的配置，示例不包含真实连接信息或凭证。
mongo_client = AsyncMongoClient(MONGODB_URL)
db = mongo_client[MONGODB_DB_NAME]

qdrant_client = AsyncQdrantClient(
    url=QDRANT_URL,
    api_key=QDRANT_API_KEY,
    cloud_inference=True,  # 将 sparse 的 BM25 文本查询交给 Qdrant 服务端。
)
embedding_client = EmbeddingClient(
    AsyncOpenAI(api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL),
    model=EMBEDDING_MODEL,
    dimensions=EMBEDDING_DIMENSIONS,
)
rerank_client = RerankClient(api_key=RERANK_API_KEY, model=RERANK_MODEL)

content_storage = MongoContentStorage(
    documents=db["document_revisions"],
    chunks=db["doc_chunks"],
)
publication_store = MongoPublicationStore(db["resource_publications"])
acl_repository = MongoResourceAclRepository(db["resource_acls"])

vector_storage = QdrantVectorStorage(
    client=qdrant_client,
    collection="document_chunk_vectors_v4",
    dense_vector_size=EMBEDDING_DIMENSIONS,
    dense_vector_name="dense",
    sparse_vector_name="sparse",
)
```

`EmbeddingClient` 在初始化时固定 model 和 dimensions，后续只调用 `embed(texts=...)`。摄入与检索应使用配置一致的客户端，dimensions 与目标 collection 一致；VectorIndexer、VectorRetriever 和 HybridRetrieval 不再单独接收 model 或 dimensions。示例假定索引已经显式初始化，Document、Chunk、向量和发布状态已经由摄入流程生成。

## 2. 装配资源快照读取

SnapshotReader 绑定内容读取、发布状态与资源授权三个依赖，负责确定本次调用可读的资源版本。

```python
from rag.application.snapshot import ResourceSnapshotReader

snapshot_reader = ResourceSnapshotReader(
    store=publication_store,
    resource_acls=acl_repository,
    reader=content_storage,
)
```

## 3. 装配完整的 application 用例

直接将已经连接的向量仓储和已绑定模型、维度的 embedding 客户端交给 HybridRetrieval。用例在每次调用中直接执行 VectorRetriever.from_storage，创建本次配置的 Retriever。

```python
from common.utils.retrieval import ModelReranker, RoundRobinFusion
from rag.application.retrieval import HybridRetrieval

reranker = ModelReranker(
    client=rerank_client,
    text_getter=lambda payload: payload.chunk.raw_text,
)

hybrid = HybridRetrieval(
    snapshot_reader=snapshot_reader,
    vector_reader=vector_storage,
    embedding_client=embedding_client,
    fusion=RoundRobinFusion(),
    reranker=reranker,
)
```

`vector_reader` 只要求 VectorReader 能力：这里传入 QdrantVectorStorage，实际查询仍由它执行。数据库连接、Storage 和模型客户端可以共享；每次构造 Retriever 只创建召回组件，不重新连接数据库。融合策略在用例装配时确定；需要 RRF 时可传入 `RrfFusion()`，省略 fusion 则使用默认 RoundRobin。

这里的 reranker 接收的是 `Candidate[PreparedChunk]`。PreparedChunk 中的正文已经通过资源授权、selected revision 和向量投影身份检查，因此文本提取函数只需要读取 Chunk.raw_text。

不需要模型重排时，可以省略 reranker；Pipeline 会保留准备后的候选顺序与分数。可选命中后处理器也在 HybridRetrieval 初始化时注入，这个示例使用直接返回 ChunkHit 的默认行为。

## 4. 发起一次检索

请求入口从可信身份得到 PermissionScope，然后为这次独立能力调用创建 ResourceSnapshot。

```python
from rag.acl.models import PermissionScope
from rag.application.snapshot import ResourceSnapshot

# trusted_user_id 和 trusted_group_roles 来自已认证的入口。
scope = PermissionScope.from_group_roles(
    user_id=trusted_user_id,
    group_roles=trusted_group_roles,
)
snapshot = ResourceSnapshot(scope)

hits = await hybrid.retrieve(
    "这份文档如何解释检索增强生成？",
    snapshot=snapshot,
    top_k=5,
    focus="优先找实现步骤和职责划分",
    diversity="moderate",
    filters=None,
    dense_top_k=30,
    sparse_top_k=30,
    candidate_limit=60,
)

for hit in hits:
    print(hit.chunk_id, hit.resource_id, hit.rank, hit.score)
    print(hit.text)
```

**from_storage 的调用位置就在 HybridRetrieval.retrieve 内部。** 这次调用展开为：

```python
from rag.retrieval.vector import VectorRetrievalMode, VectorRetriever

# HybridRetrieval 内部的装配步骤；moderate/high 需要候选已存向量。
retriever = VectorRetriever.from_storage(
    reader=vector_storage,
    embedding_client=embedding_client,
    mode=VectorRetrievalMode.HYBRID,
    fusion=RoundRobinFusion(),
    dense_top_k=30,
    sparse_top_k=30,
    candidate_limit=60,
    filters=None,
    include_embedding=True,
)

# Pipeline 接着用这个 Retriever 召回，调用合同始终只有查询文本。
candidates = await retriever.retrieve("这份文档如何解释检索增强生成？")
```

上面一段展开的是用例内部行为，阅读完整示例时无需再执行一次。`candidates` 是 `list[Candidate[VectorMatch]]`，只提供候选定位、provider 分数与可选已存向量；最终的正文命中由 application 在授权回查和排序后组装。

如果 diversity 为 off，传给 from_storage 的 include_embedding 为 False。启用多样化时，两路都请求候选已存的 dense 向量，MMR 使用通过授权检查的候选向量，不重新 embedding 候选正文。

## 5. 直接使用单路或混合 Retriever

VectorRetriever 不强制同时召回两路。from_storage 的 mode 在初始化时固定，默认 hybrid；dense 只生成查询 embedding 并做一次 dense 查询，sparse 只做一次文本查询且无需 embedding 客户端，hybrid 才并发执行两路并融合。这个边界参考 [LlamaIndex VectorIndexRetriever 的初始化模式与 embedding 判断](https://developers.llamaindex.ai/python/framework-api-reference/retrievers/vector/)，本项目用明确的 dense/sparse/hybrid 命名。

```python
dense_retriever = VectorRetriever.from_storage(
    reader=vector_storage,
    embedding_client=embedding_client,
    mode=VectorRetrievalMode.DENSE,
    dense_top_k=30,
)
sparse_retriever = VectorRetriever.from_storage(
    reader=vector_storage,
    mode=VectorRetrievalMode.SPARSE,
    sparse_top_k=30,
)
hybrid_retriever = VectorRetriever.from_storage(
    reader=vector_storage,
    embedding_client=embedding_client,
    mode=VectorRetrievalMode.HYBRID,
    fusion=RoundRobinFusion(),
)

# 所有模式都使用相同的调用合同；每次调用不修改已配置的实例。
candidates = await sparse_retriever.retrieve("检索增强生成")
```

单路保留 provider score；融合策略仅作用于 hybrid，融合分数属于 Candidate.score。candidate_limit 对所有模式限制最终候选数，dense_top_k/sparse_top_k 只约束对应已启用的路线；未启用路线不参与 I/O 或预算校验。include_embedding 在单路同样有效，sparse 也可以请求命中已有的 dense 向量，这不需要生成查询 embedding。

这里返回的仍是内部 VectorMatch 候选。面向用户的正文结果需要经过 application 的授权、版本与身份检查。HybridRetrieval 用例显式固定使用 hybrid 模式。

## 6. 运行时流转与对象生命周期

```text
hybrid.retrieve(query, snapshot=...)
  → VectorRetriever.from_storage(固定依赖 + 本次配置)
  → 本次 RetrievalPipeline.run(query)
      → VectorRetriever.retrieve(query)
          → dense：查询 embedding → VectorStorage.query
          → sparse：查询文本 → VectorStorage.query
          → Common Fusion → Candidate[VectorMatch]
      → application prepare_candidates
          → SnapshotReader.read_chunks(..., snapshot=...)
          → 资源授权、selected revision 与投影身份核对
          → Candidate[PreparedChunk]
      → ModelReranker → MMR → 截取 top_k、连续编号
  → application 组装 list[ChunkHit]
```

| 对象 | 生命周期与内容 |
| --- | --- |
| 客户端、Storage、SnapshotReader、reranker、HybridRetrieval | 可以共享；保存固定依赖与配置 |
| VectorRetriever、Pipeline、准备回调 | 每次 HybridRetrieval 调用创建；本次配置确定后不再修改 |
| ResourceSnapshot | 每次独立能力调用创建；同一次上层编排显式共享版本与权限决定 |

若上层编排还需要读取 Document，可以显式复用同一个 snapshot：

```python
documents = await snapshot_reader.read_documents(
    [hit.resource_id for hit in hits],
    snapshot=snapshot,
)
```

资源的首次版本与可见性决定会被复用。授权过滤后不补召回，结果允许少于 top_k；事实缺失、旧版本或错误投影身份省略。重排失败保留重排前顺序与分数，其他阶段异常传播。ChunkHit 只暴露正文与定位、rank、最终阶段 score，内部 metadata 和向量留在排序流程中。
