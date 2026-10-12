# Ontology 图事实抽取与离线 repair

本片只生成并保存 Chunk 图事实。生产入口目前仍只运行 ACL；下面的显式装配不代表图谱已自动接入 publication。

## 显式装配

调用方读取已有 Document 和完整 Chunk 列表，并保证同资源写入串行。无需重新分块：

```python
from common.utils.ai_clients import InstructionClient
from rag.core.schema import Ontology
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.transformers import GraphExtractor

ontology = Ontology.from_file("services/rag/demo/graph-ontology.json")
extractor = GraphExtractor(
    ontology=ontology,
    client=InstructionClient(openai_client, model=instruction_model),
    gleaning=False,
)
chunks = await extractor.transform(chunks, document=document)
await ChunkFactsIndexer(content_storage).index(chunks)
```

客户端、模型、存储、Document 和 chunks 均由调用方显式提供。GraphExtractor 不选择 applied、不判 ACL、不写存储；现有 IngestionPipeline 也可把它作为 Transformer 使用。普通 publication 的必需索引门槛不增加图谱。

Ontology、EntitySpec、RelationSpec、GraphNode 和 GraphEdge 都是 core.schema.graph 中的标准 dataclass；只有传给 Instructor 的输出 DTO 是 Pydantic。from_file 支持 .json、.yaml、.yml 和 UTF-8 BOM，使用 safe_load 解析 YAML，再在文件边界校验字段类型、未知字段、声明键值与端点类别；内部可直接构造：

```python
from rag.core.schema import EntitySpec, Ontology, RelationSpec

ontology = Ontology(
    domain="work-demo",
    entity_specs={
        "Person": EntitySpec("Person", "人员"),
        "Organization": EntitySpec("Organization", "组织"),
    },
    relation_specs={
        "works_at": RelationSpec(
            "works_at", "人员在组织任职",
            allowed_sources=["Person"], allowed_targets=["Organization"],
        ),
    },
)
```

配置文件可自行集中放入 ontologies/，from_file 不扫描目录或建立 Registry。

可直接对比 [JSON 示例](graph-ontology.json) 和 [YAML 示例](graph-ontology.yaml)：两份配置表达相同本体，允许 Person → works_at → Organization，YAML 版本附有字段说明注释。加载时只需替换文件路径：

```python
ontology = Ontology.from_file("services/rag/demo/graph-ontology.yaml")
```

材料只有当前 raw_text 和 section_path。标题只帮助消歧，不读取生成上下文、邻居或全文。节点来源由程序绑定当前 Chunk ID；关系 source_id/target_id 是节点 ID，source_ids 才是来源 Chunk 列表。

成功时替换 entities、relationships、graph_version，保留其他 metadata。内容存储读回事实列表仍为普通字典。有效空结果也缓存；指纹过期或缓存损坏/来源不符时重算整块。每实例绑定一个 Ontology，不在同一 metadata 中累积多个本体。

## 补漏与失败

默认关闭补漏；gleaning=True 最多追加一轮，复用当前块首次对话。提示词只允许增量事实，无增量返回空列表。首轮合法 ID 的字段始终保留，新关系可以引用首轮实体。不合法类别、谓词和端点逐条丢弃，日志记录拒绝原因。

默认输入预算 8000 token、每轮输出上限 4096 token、共享实例模型并发上限 4。预算包含渲染消息和结构化 schema 文本，不等于供应商聊天封装的精确 token 数。首轮超预算报错且不截断原文；补漏历史超预算记录日志并保留首轮结果。Instructor 自身纠错重试可能增加 HTTP 次数，不是补漏。

模型/整份响应异常、重试耗尽和取消传播；其他任务取消后等待收尾。所有块成功后才修改图 metadata，调用方随后保存。图失败不撤销普通发布；写入失败不回滚部分成功。

## 离线 repair

维护窗口内禁止同资源其他 Chunk 写任务。通过 CLI 或专用环境变量提供连接配置：

```text
RAG_GRAPH_REPAIR_MONGO_URL
RAG_GRAPH_REPAIR_MONGO_DATABASE
RAG_GRAPH_REPAIR_INSTRUCTION_BASE_URL
RAG_GRAPH_REPAIR_INSTRUCTION_API_KEY
RAG_GRAPH_REPAIR_INSTRUCTION_MODEL
```

从仓库根目录执行：

```powershell
.venv/Scripts/python.exe services/rag/scripts/repair_graph.py `
    --ontology-file services/rag/demo/graph-ontology.json `
    --resource-id RESOURCE_ID
```

资源可重复传 --resource-id，或用 --resource-file 指定每行一个 ID 的 UTF-8 文件，去重保序。连接配置也可用 --mongo-url、--mongo-database、--instruction-base-url、--instruction-api-key、--instruction-model 显式传入。

--gleaning 开启补漏；--force 禁用增量复用。预算和并发使用 --max-input-tokens、--max-output-tokens、--max-concurrency。

repair 选择当前 applied 未 destroyed 的资源，核对完整正文身份并读取既有块；写前复核完整发布状态，变化则跳过。成功后保存全部 Chunk，即使全由缓存复用。资源失败继续下一资源，输出 repaired/skipped/document_errors；有错误退出 1，否则退出 0。setup 失败退出 1，取消传播，全部客户端关闭。

脚本不加载 Nacos、不 stage/apply、不启动消费者、不重分块、不重写普通向量或图拓扑。状态复核遵循维护窗口约定，不提供并发更新协议。
