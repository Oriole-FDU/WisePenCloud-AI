# RAG 普通向量投影与发布闭环实施记录

记录日期：2026-10-12。本轮完成交接文档建议顺序的第 1–4 步；第 5 步生产配置与入口装配单独继续。代码只写在新 `services/rag`，没有改变 publication 状态合同或旧运行时。

## 实际模型与能力边界

- `core/schema/vector.py`：VectorRecord、VectorQuery、VectorMatch 为 frozen/slots dataclass。Record 使用原业务 record_id、text、dense embedding 和普通 dict metadata；Query 的 embedding/text 二选一；Match 仅包含候选身份、分数和 metadata。
- `core/schema/filters.py`：MetadataFilter/MetadataFilters、FilterOperator/FilterCondition；支持 EQ、IN、GT/GTE/LT/LTE 和递归 AND/OR/NOT。
- `storage/vector.py`：VectorReader、VectorWriter、VectorStorage Protocol。写入与删除返回 None，空 upsert 无 I/O。没有 Receipt、Repository、Router、namespace 或新增发布状态。
- `backends/qdrant/vector.py`：QdrantVectorStorage 绑定 collection，客户端、维度和 named vector 配置显式注入。`filters.py` 负责 provider 条件转换，不理解 ACL、active 或插件。
- `ingestion/indexers/vector.py`：VectorIndexer 只读最终 Chunk，调用 Common EmbeddingClient 并等待 VectorWriter 完成。默认 embedding batch 为 32；全部批次成功才返回。

Collection 名称由调用方提供；用户确认沿用现有配置键，但使用独立 V4 集合，后续生产默认选择 `document_chunk_vectors_v4`，named vectors 为 dense/sparse。本轮尚未修改 AppSettings/Nacos 或生产 Container/main。

## Qdrant 与失败合同

初始化显式调用 ensure_collection：不存在时创建 dense Cosine、sparse IDF；存在时核对对应配置，冲突报错，不删除集合。显式初始化每次都保证 metadata.resource_id/content_revision keyword 索引，允许上次在建集合或索引后部分失败的初始化继续执行。普通 CRUD 不隐式创建集合。

Point ID 固定为 `uuid5(NAMESPACE_URL, f"wisepen-rag-v4:vector:{record_id}")`，核心 Chunk ID 不变。payload 形态为 `{"record_id": ..., "metadata": {"resource_id": ..., "content_revision": ...}}`，完整替换，不保存正文、retrieval_text、ACL 或未来图字段。

每个 point 同时提交 dense 数值向量与 `Document(text=record.text, model="qdrant/bm25", options={"tokenizer": "multilingual"})`。Qdrant client 必须使用 `cloud_inference=True`，否则 1.18 客户端可能尝试本地推理。写入和查询固定相同文本处理配置。依据 [官方客户端源码](https://raw.githubusercontent.com/qdrant/qdrant-client/v1.18.0/qdrant_client/async_qdrant_client.py) 和 [BM25 文档](https://qdrant.tech/documentation/search/text-search/full-text-search/) 实现；真实客户端转发测试替换网络边界，确认两种请求保留 Document。

Qdrant 默认每批 256 条，串行 upsert。upsert/delete/索引创建使用 wait=True 并核对 completed；acknowledged 和 wait_timeout 都不能作为成功。参见 [Qdrant 写入确认](https://qdrant.tech/documentation/manage-data/points/)。后端无自动重试；已写入批次不回滚。Query 根据输入选择 dense 或 sparse，返回 provider 顺序与原 record_id；坏 payload 明确报错，不伪装成空结果。

过滤组非空，NOT 只有一个子条件；IN 为非空同类型字符串或整数列表；范围比较只接受有限数值，排除 bool。浮点 EQ 转换为闭区间，避免 MatchValue 的类型限制。查询可省略 filters，删除必须提供非空条件。

VectorIndexer 每批先 embedding 再等待向量写入，然后进入下一批。只读取 Chunk.retrieval_text，不修改 Chunk；metadata 只投影资源/revision。核对供应商返回的数量、维度和有限数值；Common 抛出的响应 ValueError 包装为 RuntimeError，避免被 publication 的永久输入错误分类送入 DLQ。I/O 直接传播。零 Chunk 不访问 embedding 或数据库。

## 发布装配与验收

外部固定普通配置：

```python
vectors = QdrantVectorStorage(
    client=qdrant_client,  # 外部创建：cloud_inference=True，设置请求超时。
    collection="document_chunk_vectors_v4",
    dense_vector_size=embedding_dimensions,
)
await vectors.ensure_collection()
pipeline = IngestionPipeline(
    chunker=MarkdownChunker(),
    transformers=[],
    indexers=[
        ChunkFactsIndexer(writer=content),
        VectorIndexer(
            embedding_client=embedding_client,
            model=embedding_model,
            dimensions=embedding_dimensions,
            writer=vectors,
        ),
    ],
)
publisher = DocumentPublication(
    store=publication_store, reader=content, writer=content, pipeline=pipeline,
)
```

publication 与 Pipeline 的生产代码没有增加装配检查或向量回查。成功仍为 stage → Document → Chunk facts → 全部必需向量写入 → 内容事实回查 → 条件 apply。任一向量失败保留旧 applied、候选 staged 和已写入事实；同身份 staged 重放可覆盖 point 后完成，已 applied 同身份仍跳过。构建期间新版本或销毁到达仍阻止旧候选 apply。

本轮增加 72 个通过用例及 3 个可选 Qdrant 集成用例。全服务结果：**274 passed、17 skipped**，其中跳过 11 Mongo、3 Kafka、3 Qdrant。Ruff check、修改文件 format check、源码与测试 compileall、git diff --check 和新文件空白检查均通过。共享 core.schema 导入测试确认不加载 Qdrant、storage、backends、ingestion、application 或运行配置。

真实 Qdrant fixture 使用 `RAG_TEST_QDRANT_URL` 与可选 `RAG_TEST_QDRANT_API_KEY`，只创建/清理随机 collection。未配置时跳过，配置后连接失败或服务端 BM25 不支持必须失败。测试覆盖中文 BM25、dense、revision 过滤、重复写入替换和删除，以及真实 Qdrant 参与 publication。后一个用例仍以 fake embedding 和内存 Mongo 为边界，不能宣称所有外部后端联调完成。本环境未配置真实 Qdrant，服务端版本与 BM25 multilingual 支持尚未验证。

## 下一切片

先确认生产配置、模型/维度值、独立集合及必要初始化时序，再接 Container/main 的 publication 生命周期和健康状态；客户端资源由入口显式关闭。随后实现 selected revision/ACL 读取与普通 hybrid Retriever。Contextualizer、物理清理、投影重建及图谱仍未实现；同 revision 换分块配置后的旧 point 清理不包含在当前 upsert 合同中。
