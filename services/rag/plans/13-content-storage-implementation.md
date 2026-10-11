# 统一内容模型、ContentStorage 与 Mongo ChunkFactsIndexer 实施记录

后续结构化读取已扩展 ContentReader.get_documents_by_section_ids 和 Mongo 普通章节索引，按请求章节的首次匹配顺序返回去重 Document，见 [20 实施记录](20-structured-reading-implementation.md)。查询不解释 ACL 或 publication；共享快照 Reader 负责正文身份、版本与授权检查。下文验收结果保留内容存储切片的历史记录。

## 完成能力

- `core/schema/content.py` 统一定义 Document、DocumentStructure、Chunk，字段、默认值、slots 和 retrieval_text 行为保持不变。`core.schema` 导出三种内容模型及图模型；ingestion 从 schema 导入模型，不保留旧 document 模块或 ingestion 的 Chunk 导出。
- `storage/content.py` 定义 ContentReader、ContentWriter 两个接口，ContentStorage 组合二者。存储能力统一，Document 与 Chunk 模型和访问方法保持独立。
- `backends/mongo/content.py` 的 MongoContentStorage 显式绑定两个 AsyncCollection；当前 Document、Chunk 映射、批量操作与索引已统一收敛在此文件，早期 chunk／document 子包不再保留。没有 Repository、Storage Router、全局连接或配置依赖。
- `ingestion/indexers/chunk.py` 的 ChunkFactsIndexer 只调用 ContentWriter.upsert_chunks，接入现有有序 Pipeline；不修改输入、不维护 Document 或发布状态。

## 存储合同

| 能力 | 行为 |
| --- | --- |
| get_documents | 按 `(resource_id, content_revision)` 批量读取；输入去重并保序，缺失对象省略 |
| get_chunks | 按 chunk_id 批量读取；输入去重并保序，缺失对象省略 |
| get_revision_chunks | 按指定资源和 revision 读取，按 chunk_index 升序 |
| upsert_documents / upsert_chunks | 保存完整模型；Document 按资源/revision，Chunk 按 chunk_id；每批 500 条有序 ReplaceOne upsert |
| delete_revisions / delete_resources | 按精确 revision 或资源同时物理删除 Document、Chunk；调用方先撤销可见性 |
| ensure_indexes | 显式初始化身份唯一索引；不属于 ContentStorage 接口，也不在普通读写时隐式执行 |

Document 与 Chunk 直接使用 Pydantic TypeAdapter 展开和恢复。结构中的 Common dataclass 保持原模型；metadata 内图模型保存为普通字典，读回不自动恢复图模型。`retrieval_text` 等 property 不持久化，也不引入 metadata schema、codec 或存储镜像模型。

空批次无 I/O。相同身份重复写入幂等；metadata 随完整对象替换，不隐式合并。upsert 不删除批次外 Chunk，不提供同 revision 的整版重新分块替换。BSON 不支持的对象正常报错，不转换为字符串。多批写入和跨集合删除不建立事务，后续失败保留前面已完成的操作，不重试或补偿。

## 使用方式

```python
from rag.backends.mongo.content import MongoContentStorage
from rag.ingestion import IngestionPipeline
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer

storage = MongoContentStorage(
    documents=database["document_revisions"],
    chunks=database["doc_chunks"],
)
await storage.ensure_indexes()  # 后端初始化时调用一次。

await storage.upsert_documents([document])
pipeline = IngestionPipeline(
    chunker=MarkdownChunker(),
    transformers=[],
    indexers=[ChunkFactsIndexer(writer=storage)],
)
await pipeline.run(document)
```

示例只表示内容落库，不表示 publication 完成。Mongo 客户端、database 和 Document 由外部调用方提供；本轮未接入 Container、Nacos 或 Kafka。

## 发布与下一阶段

后续运行时统一为：

```text
校验内容事件、构造 revision、判断版本与内容冲突
  -> 条件 stage，保留旧 applied
  -> ContentWriter 保存 Document
  -> 普通 Pipeline：Chunker / Transformer / ChunkFactsIndexer / VectorIndexer
  -> 确认内容与必需检索投影完整，条件 apply
  -> 普通内容摄入成功后确认对应消息处理完成
  -> 独立构建可选图谱
```

stage 允许事实尚未完整。Mongo 内容写入成功不触发 apply。删除先撤销 staged/applied，再清理内容与投影；读取固定 applied revision 后回查内容并检查本地 ACL。不假定内容、ACL 和删除事件跨 topic 有序，也不把 Common 当前的消费循环当成已经验证的失败重试合同。

内容身份与 publication 的后续实施见 14：publication 位于 application.publication，作为资源变更到可检索内容的应用用例；用例流程状态存于独立 resource_publications，不改变 ContentStorage。Pipeline 全部成功后返回最终列表，发布用例回查正文身份和精确 Chunk 集合；必需投影依赖 Indexer 成功合同，不增加独立向量完整性查询。后续接真实 VectorIndexer、用例内事件确认与重试，再扩展物理清理与可选图谱。

## 验证

从仓库根目录执行：

| 命令 | 结果 |
| --- | --- |
| `uv run --project services/rag pytest -q services/rag/tests` | 63 passed，3 skipped |
| `uv run --project services/rag ruff check services/rag/src/rag services/rag/tests` | 通过 |
| `uv run --project services/rag python -m compileall -q services/rag/src/rag` | 通过 |
| `git diff --check` | 通过 |

已有 35 个测试继续通过，新增 28 个本地用例覆盖模型归属、BSON 往返、普通图 metadata、批量顺序、幂等替换、资源/revision 隔离、分批写入、部分失败保留、精确删除和实际 Markdown Pipeline 落库顺序。fake collection 不模拟 Mongo 唯一索引，唯一索引和真实读写由集成测试验证。

3 个真实 Mongo 集成测试因未配置 `RAG_TEST_MONGODB_URI` 跳过。显式配置测试连接后执行：

```powershell
uv run --project services/rag pytest -q services/rag/tests/backends/mongo/test_content_integration.py
```

每个 fixture 创建并只清理自己的 `codex_rag_content_test_<随机ID>` 数据库。配置存在但连接失败时测试报错，不转成跳过。表中 63/3 是本内容存储阶段结果；后续 publication 与当前验证见 14。真实向量/图谱投影和上游事件接入仍未验证。
