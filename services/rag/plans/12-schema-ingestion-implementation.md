# RAG V4 Schema 与 Ingestion 实施记录

## 已完成范围

实现位于 `services/rag/src/rag`。本记录按当前模型归属更新，内容存储扩展与完整验证见 13 实施记录。

- `core/schema/content.py`：Document、DocumentStructure、Chunk 使用独立 dataclass，共同作为 ingestion 和 Storage 的框架合同；直接复用 Common Section 和 Anchor，文档正文只有 `raw_markdown`。
- `core/schema/graph.py`：GraphNode、GraphEdge 使用简单 Pydantic 模型，仅包含方案指定字段。节点名称只进行首尾空白清理、casefold 和连续空白归一；缺失 category 时 ID 保留空槽。边 ID 保持方向和原始 relation_type。
- Chunk 保留指定的十个字段；`retrieval_text` 只从普通 metadata 的 `contextual_context` 组装。Chunker 接口不再定义模型，旧 document 模块和 ingestion 的 Chunk 导出已移除。
- `ingestion/chunkers/base.py`、`transformers/base.py`、`indexers/base.py`：分别定义三个 Protocol；Transformer 可接收当前 Document。
- `ingestion/chunkers/markdown.py`：调用 Common `.chunk(document.raw_markdown)`，适配正文、顺序、token 数、section path 和 anchor labels；不复制内部 spans、节点、overflow 或 content hash。
- `ingestion/pipeline.py`：Chunker 执行一次，Transformer 依次传递返回列表，Indexer 依次串行消费最终列表；异常原样传播，不重试、不回滚、不复制 Chunk。
- `tests/core/test_schema.py`、`tests/ingestion/test_pipeline.py`、`tests/ingestion/test_markdown.py`：覆盖模型合同、归属、顺序、对象 identity、metadata 增强、新列表交接、逐阶段失败和真实 Common 分块适配。

RAG Chunk 和 section 的本地 ID 格式为：

```text
{resource_id}:{content_revision}:{Common ID}
```

DocumentStructure 保留 Common 原始结构，不由 Chunker 重写；Chunk.section_id 在适配时加上资源与 revision 前缀。document_version 不参与 Chunk identity。

## 验证结果

从仓库根目录执行：

| 命令 | 结果 |
| --- | --- |
| `uv run --project services/rag pytest -q services/rag/tests` | 63 passed，3 skipped；跳过项为未配置连接的真实 Mongo 测试 |
| `uv run --project services/rag ruff check services/rag/src/rag services/rag/tests` | 通过 |
| `uv run --project services/rag python -m compileall -q services/rag/src/rag` | 通过 |
| `git diff --check` | 通过 |

当前 ACL 测试通过；本次内容模型与存储实现未修改 ACL 源码或测试。

## 后续边界

当前已扩展 ContentStorage、MongoContentStorage 和 ChunkFactsIndexer，详见 13。GraphExtractor、VectorIndexer、GraphIndexer、Retriever、publication 和 application 调用方仍未实现。

后续组件从各自模块的 Protocol 扩展，继续遵守普通 metadata、只读 Indexer、串行失败即停的合同。真实 Mongo 连接、向量与图后端、LLM 抽取及应用发布行为留待对应阶段验证。
