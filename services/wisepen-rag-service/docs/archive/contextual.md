# 上下文增强与离线 repair 装配

平台默认装配与共享客户端生命周期现已在 Container/main 实现，见 [platform.md](platform.md)；下文保留组件级显式组合。垂类注入与
provider 覆盖见 [extensions.md](../src/rag/extensions/extensions.md)。

普通摄入在分块后生成上下文，先落库最终 Chunk，再写 dense 与服务端 BM25。模型固定在 InstructionClient 初始化时，Contextualizer
只负责材料、提示词和可选增强；连接与模型配置由装配入口传入。参考方法见 [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
。这里的 8000 token 是完整提示文本输入上限，包含目标 Chunk、指令、分隔符及局部模式下的 metadata/大纲。

## 普通 Pipeline

下面使用 [hybrid.md](hybrid.md) 中已经装配的 `content_storage`、`vector_storage`、`embedding_client`、`publication_store`
，大写值由外部配置提供。首次启动应显式执行 storage 索引与 collection 初始化；模型、Mongo 与 Qdrant 客户端由入口负责关闭。示例展示组件组合，不接入生产
Container 或消费者。

```python
from openai import AsyncOpenAI
from common.utils.ai_clients import InstructionClient
from rag.application.publication import DocumentPublication, DocumentReadyEvent
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.transformers import Contextualizer
from rag.ingestion.indexers import ChunkFactsIndexer, VectorIndexer
from rag.ingestion.pipeline import IngestionPipeline

instruction_api = AsyncOpenAI(
    base_url=INSTRUCTION_BASE_URL,
    api_key=INSTRUCTION_API_KEY,
)
instruction = InstructionClient(instruction_api, model=INSTRUCTION_MODEL)
contextualizer = Contextualizer(
    client=instruction,
    max_context_tokens=8000,
    target_context_chars=100,
    max_output_tokens=150,
    before_ratio=0.5,
    outline_token_ratio=0.15,
    max_concurrency=4,
)

pipeline = IngestionPipeline(
    chunker=MarkdownChunker(),
    transformers=[contextualizer],
    indexers=[
        ChunkFactsIndexer(content_storage),
        VectorIndexer(embedding_client=embedding_client, writer=vector_storage),
    ],
)
publisher = DocumentPublication(
    store=publication_store,
    reader=content_storage,
    writer=content_storage,
    pipeline=pipeline,
)
document = DocumentReadyEvent.model_validate({
    "resourceId": "example-resource",
    "version": 1,
    "content": "# 配置手册\n\n配置说明。\n\n## 超时\n\n请求超时为 30 秒。",
}).to_document()
action = await publisher.publish(document)
```

全文能放入完整提示词预算时直接使用；否则选择可容纳的最高章节祖先子树，再降到完整 own 范围或目标 Chunk 种子，向两侧连续扩展
Chunk。窗口可跨同文档的章节，不跨 resource/revision。大纲仅为标题骨架，有独立 token 上限，优先保留当前祖先与邻近兄弟。所有可行性判断计数实际提示文本，邻居
token 权重仅用于前后比例选择。

最终检索输入示意：

```text
配置手册>超时

本段来自配置手册，说明请求超时设置。

请求超时为 30 秒。
```

路径采用原始 section_path，生成上下文存 `metadata["contextual_context"]`；`raw_text` 仍为原始 Chunk 正文。dense 与 BM25
使用同一 retrieval_text，响应、章节读取和 rerank 使用原文。失败时清除旧上下文，保留路径和原文；`context_status` 为
ok/fallback/failed，成功才保存 `context_version`。语言与事实由提示词约束，程序只检查非空、截断和输出预算，不保证模型没有幻觉。

## 已发布事实的离线 repair

重复提交同一已 applied 版本仍然跳过，不能借重放重新生成上下文。维护窗口内使用专用脚本修复明确列出的资源。资源文件每行一个
ID，重复项按首次出现顺序保留；不传资源范围会直接拒绝运行。

连接和模型配置必须通过专用环境变量或同名 CLI 参数提供：

| 环境变量                                                                                   | CLI 参数                                                                                |
|--------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| RAG_REPAIR_MONGO_URL / MONGO_DATABASE                                                      | --mongo-url / --mongo-database                                                          |
| RAG_REPAIR_QDRANT_URL / QDRANT_API_KEY                                                     | --qdrant-url / --qdrant-api-key（凭证可选）                                             |
| RAG_REPAIR_VECTOR_COLLECTION                                                               | --vector-collection（默认 document_chunk_vectors_v4）                                   |
| RAG_REPAIR_INSTRUCTION_BASE_URL / INSTRUCTION_API_KEY / INSTRUCTION_MODEL                  | --instruction-base-url / --instruction-api-key / --instruction-model                    |
| RAG_REPAIR_EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL / EMBEDDING_DIMENSIONS | --embedding-base-url / --embedding-api-key / --embedding-model / --embedding-dimensions |

表中每个斜线后名称均带 `RAG_REPAIR_` 前缀。例如数据库为 `RAG_REPAIR_MONGO_DATABASE`。凭证可放在环境变量中。现有内容集合固定为
`document_revisions`、`doc_chunks` 和 `resource_publications`；脚本不加载 Nacos 配置、不启动消费者。

从仓库根目录，在上述配置已提供后运行：

```powershell
.venv/Scripts/python.exe services/rag/scripts/repair_context.py `
    --resource-id example-resource `
    --resource-id another-resource `
    --max-context-tokens 8000 `
    --target-context-chars 100 `
    --max-output-tokens 150 `
    --before-ratio 0.5 `
    --outline-token-ratio 0.15 `
    --max-concurrency 4

# 或用文件提供资源范围：
.venv/Scripts/python.exe services/rag/scripts/repair_context.py --resource-file resources.txt
```

脚本只读取当前 applied 且未 destroyed 的 revision，验证完整 Document 身份并复用已有
Chunk，生成后、写入前再次核对发布状态。期间状态变化则跳过写入。目标资源应没有其他任务并发写
Chunk；这是维护窗口的操作约定，发布状态复核不构成跨数据库并发修复协议。

指纹匹配的成功上下文跳过 LLM，但 **每次都重写所选文档的全部 Chunk 向量**。因此上次“Chunk
事实已保存、向量写入失败”后，重跑可以复用上下文并补齐投影。模型失败按块清除旧上下文，仍写事实和路径/正文向量；数据库或向量异常按文档记录，继续其他资源，已有写入保留。结束报告
generated/reused/fallback/failed/skipped/repaired/document_errors；failed 或 document_errors 非零时退出码为 1，预算
fallback 本身不导致非零退出。

脚本不 stage/apply、不重新分块、不改变 Document 或 Chunk 身份。它显式核对/初始化目标向量
collection，所有客户端在结束或初始化失败后关闭。网络重试沿用 SDK 装配，没有脚本级重试。本轮仅用替身验证，没有对现有数据库执行
repair；父块、在线重构建、调度及生产生命周期仍留后。
