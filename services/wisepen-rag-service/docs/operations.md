# 离线修复与 DLQ 手动重放手册

维护入口是 [repair_context.py](../scripts/repair_context.py) 和 [repair_graph.py](../scripts/repair_graph.py)。本文说明真实参数与写入范围，模型/缓存原理见[核心抽象](abstractions.md)，正常服务配置见[运行说明](deployment.md)。

Kafka 失败消息使用 [replay_dlq.py](../scripts/replay_dlq.py) 按位置手动重放，详见第 6 节；不自动回投。

## 1. 操作前提与模式选择

在维护窗口内保证同资源没有在线发布、其他 repair 或 Chunk 写任务。脚本写前会复核完整发布状态，但没有锁/租约，无法消除复核之后的并发写入。只处理显式列表中当前 applied 且未 destroyed 的资源，不全库扫描。

读取完整 Document 并核对资源/version/原文摘要/revision，然后读取当前既有 Chunk；不重新分块。缺资源/无 applied/destroyed 跳过，缺文档或身份不符按资源错误报告。两脚本不加载 Nacos、不改 stage/apply、不修改 ACL；使用独立 CLI/环境变量配置。

| 需求 | 入口/模式 | 写入范围 |
|---|---|---|
| 补齐或刷新生成上下文 | repair_context | 当前全部 Chunk 事实 + 全部普通 Chunk 向量 |
| 重抽/复用图事实 | repair_graph 默认 | 当前全部 Chunk 事实；无投影参数时不写图数据库 |
| 图事实及拓扑 | 默认 + --with-graph | 事实后写全部来源拓扑 |
| 图事实及来源向量 | 默认 + --with-graph-vectors | 事实后写实体/关系两集合 |
| 只重建既有拓扑 | --projection-only --with-graph | 不抽取、不写 Chunk，仅来源拓扑 |
| 只重建既有图向量 | --projection-only --with-graph-vectors | 不抽取、不写 Chunk，仅两种来源向量 |
| 两种图投影一起重建 | --projection-only + 两种 with 参数 | GraphIndexer 先拓扑再图向量 |

图脚本不重写普通 Chunk 向量；两个通用脚本均不重建 Employment 等业务专用集合，业务示例见[Employment 维护](employment.md)。

## 2. 共同输入与执行目录

以下 PowerShell 命令从仓库根目录执行，使用已经安装新 RAG/Common 包的 `.venv/Scripts/python.exe`。示例集合和模型值必须与目标当前配置一致；配置不匹配不会自动重建集合。

两脚本都支持重复 `--resource-id ID`，或 `--resource-file PATH`（UTF-8/UTF-8 BOM，每行一个 ID），也可组合。首尾空白和空行去掉，去重保留顺序；不要写注释行，脚本会将其当作 ID。缺有效资源列表是参数错误。

```powershell
.venv/Scripts/python.exe services/wisepen-rag-service/scripts/repair_context.py --help
.venv/Scripts/python.exe services/wisepen-rag-service/scripts/repair_graph.py --help
```

这两条仅展示帮助，不创建客户端或执行修复。以下实际执行示例需在上述维护窗口运行。凭据用专用环境变量提供；CLI 同名选项覆盖环境变量。

## 3. 上下文 repair 配置

连接/模型选项的环境变量为 `RAG_REPAIR_` + 大写参数名（连字符改下划线）。生成策略参数仅通过 CLI 配置。

| CLI | 环境变量后缀 | 必填/默认 |
|---|---|---|
| --mongo-url / --mongo-database | MONGO_URL / MONGO_DATABASE | 必填 |
| --qdrant-url / --qdrant-api-key | QDRANT_URL / QDRANT_API_KEY | URL 必填，key 可选 |
| --vector-collection | VECTOR_COLLECTION | document_chunk_vectors_v4 |
| --instruction-base-url / --instruction-api-key / --instruction-model | INSTRUCTION_BASE_URL / INSTRUCTION_API_KEY / INSTRUCTION_MODEL | 全部必填 |
| --embedding-base-url / --embedding-api-key / --embedding-model | EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL | 全部必填 |
| --embedding-dimensions | EMBEDDING_DIMENSIONS | 正整数，必填 |
| --max-context-tokens | 无 | 8000，完整提示词预算，正数 |
| --target-context-chars | 无 | 100，目标字数，正数 |
| --max-output-tokens | 无 | 150，输出硬上限，正数 |
| --before-ratio | 无 | 0.5，范围 [0,1] |
| --outline-token-ratio | 无 | 0.15，范围 [0,1] |
| --max-concurrency | 无 | 4，正整数，实例模型并发 |

例如 `--instruction-api-key` 对应 `RAG_REPAIR_INSTRUCTION_API_KEY`，不是平台 INSTRUCTION_API_KEY。上下文脚本没有 --force 或 --projection-only。

### 执行示例

```powershell
$env:RAG_REPAIR_MONGO_URL = 'mongodb://localhost:27017'
$env:RAG_REPAIR_MONGO_DATABASE = 'rag_v4'
$env:RAG_REPAIR_QDRANT_URL = 'http://localhost:6333'
$env:RAG_REPAIR_INSTRUCTION_BASE_URL = 'https://your-instruction-gateway/v1'
$env:RAG_REPAIR_INSTRUCTION_API_KEY = '<instruction-key>'
$env:RAG_REPAIR_INSTRUCTION_MODEL = '<instruction-model>'
$env:RAG_REPAIR_EMBEDDING_BASE_URL = 'https://your-embedding-gateway/v1'
$env:RAG_REPAIR_EMBEDDING_API_KEY = '<embedding-key>'
$env:RAG_REPAIR_EMBEDDING_MODEL = '<embedding-model>'
$env:RAG_REPAIR_EMBEDDING_DIMENSIONS = '1024'
.venv/Scripts/python.exe services/wisepen-rag-service/scripts/repair_context.py `
    --resource-file .maintenance/resource-ids.txt `
    --vector-collection document_chunk_vectors_v4
$LASTEXITCODE
```

资源文件自行准备，1024 是示例，必须替换成目标集合真实维度；需要 Qdrant key 时设置 RAG_REPAIR_QDRANT_API_KEY。普通向量需要 Qdrant 服务端 BM25 推理能力，不降级 dense-only。

### 写入、缓存与报告

成功上下文且指纹有效时复用；过期/failed 重试。即使所有块复用，每次仍保存全部 Chunk 并重写全部普通向量，因为生成缓存不证明上次向量写成功。

模型/输出失败逐块清旧上下文，标记 failed，仍保存降级事实和向量，最终非零退出。预算不足/空目标为 fallback，不单独导致非零。文档级错误保留部分写入并继续下一个资源；无脚本自动重试。

正常运行结束输出一行 JSON，字段 generated/reused/fallback/failed 为块级统计，skipped/repaired/document_errors 为资源级统计。块统计不能当作持久化成功数；repaired 表示事实及向量都完成。failed 或 document_errors 任一非零时退出 1。

## 4. 图 repair 配置

连接/模型选项使用 `RAG_GRAPH_REPAIR_` 前缀，与上下文脚本互不混用。ontology-file、资源列表和策略/模式开关只通过 CLI。

| CLI | 环境变量后缀 | 要求/默认 |
|---|---|---|
| --ontology-file | 无 | 所有模式必填，JSON/YAML/YML；连接前解析 |
| --mongo-url / --mongo-database | MONGO_URL / MONGO_DATABASE | 所有模式必填 |
| --instruction-base-url / --instruction-api-key / --instruction-model | INSTRUCTION_BASE_URL / INSTRUCTION_API_KEY / INSTRUCTION_MODEL | 非 projection-only 时必填 |
| --max-input-tokens / --max-output-tokens / --max-concurrency | 无 | 8000 / 4096 / 4，均正数 |
| --gleaning | 无 | 默认关闭，最多一次补漏 |
| --force | 无 | 默认关闭，禁用抽取缓存复用 |
| --with-graph / --with-graph-vectors | 无 | 默认关闭，分别启用拓扑/图向量 |
| --projection-only | 无 | 默认关闭，至少选择一种投影；不能同时 force/gleaning |
| --neo4j-uri / --neo4j-username / --neo4j-password | NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD | with-graph 时必填 |
| --neo4j-database / --neo4j-graph-name | NEO4J_DATABASE / NEO4J_GRAPH_NAME | neo4j / rag_v4；graph-name 为合法 Cypher 标识符 |
| --qdrant-url / --qdrant-api-key | QDRANT_URL / QDRANT_API_KEY | with-graph-vectors 时 URL 必填，key 可选 |
| --embedding-base-url / --embedding-api-key / --embedding-model | EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL | with-graph-vectors 时全部必填 |
| --embedding-dimensions | EMBEDDING_DIMENSIONS | with-graph-vectors 时必填正整数 |
| --entity-vector-collection | ENTITY_VECTOR_COLLECTION | graph_entity_source_vectors_v4 |
| --relation-vector-collection | RELATION_VECTOR_COLLECTION | graph_relation_source_vectors_v4；与实体集合不同 |

projection-only 不创建抽取客户端、不写 Chunk，但仍要求本体，用于既有事实解析和投影。向量模式仍调用 embedding。force 只禁用缓存，不表示删除全部历史来源。

### 只修复图事实

```powershell
$env:RAG_GRAPH_REPAIR_MONGO_URL = 'mongodb://localhost:27017'
$env:RAG_GRAPH_REPAIR_MONGO_DATABASE = 'rag_v4'
$env:RAG_GRAPH_REPAIR_INSTRUCTION_BASE_URL = 'https://your-instruction-gateway/v1'
$env:RAG_GRAPH_REPAIR_INSTRUCTION_API_KEY = '<instruction-key>'
$env:RAG_GRAPH_REPAIR_INSTRUCTION_MODEL = '<instruction-model>'
.venv/Scripts/python.exe services/wisepen-rag-service/scripts/repair_graph.py `
    --resource-file .maintenance/resource-ids.txt `
    --ontology-file services/wisepen-rag-service/src/rag/extensions/employment/ontology.yaml
```

此命令不写拓扑/图向量。需要同步投影时追加相应 with 参数并配齐对应连接/模型；不要误以为默认会更新所有查询投影。

### 旧拓扑补 ontology_name

在上述 Mongo 环境上设置：

```powershell
$env:RAG_GRAPH_REPAIR_NEO4J_URI = 'neo4j://localhost:7687'
$env:RAG_GRAPH_REPAIR_NEO4J_USERNAME = 'neo4j'
$env:RAG_GRAPH_REPAIR_NEO4J_PASSWORD = '<neo4j-password>'
.venv/Scripts/python.exe services/wisepen-rag-service/scripts/repair_graph.py `
    --resource-file .maintenance/resource-ids.txt `
    --ontology-file services/wisepen-rag-service/src/rag/extensions/employment/ontology.yaml `
    --projection-only --with-graph
```

不调用抽取，不写 Mongo；用已有合法事实补齐来源投影本体属性。只有维护列表内当前来源被替换，未传入的历史 revision/旧分块来源不会回收。

### 只重建图来源向量

在图脚本的 Mongo 环境上设置：

```powershell
$env:RAG_GRAPH_REPAIR_QDRANT_URL = 'http://localhost:6333'
$env:RAG_GRAPH_REPAIR_EMBEDDING_BASE_URL = 'https://your-embedding-gateway/v1'
$env:RAG_GRAPH_REPAIR_EMBEDDING_API_KEY = '<embedding-key>'
$env:RAG_GRAPH_REPAIR_EMBEDDING_MODEL = '<embedding-model>'
$env:RAG_GRAPH_REPAIR_EMBEDDING_DIMENSIONS = '1024'
.venv/Scripts/python.exe services/wisepen-rag-service/scripts/repair_graph.py `
    --resource-file .maintenance/resource-ids.txt `
    --ontology-file services/wisepen-rag-service/src/rag/extensions/employment/ontology.yaml `
    --projection-only --with-graph-vectors
```

实体/关系集合 dense-only；该模式适合缺 fact_sha256 的旧投影。需要同时更新拓扑时追加 --with-graph。仅投影不能为旧 Mongo 实体补 relations 提示，须正常 repair（必要时 --force）重抽事实，再更新投影。

### 报告与失败恢复

图 JSON 报告为 repaired/skipped/document_errors，均为资源级。document_errors 非零退出 1；抽取失败不进入事实 Indexer。事实成功、拓扑/向量失败时保留已完成写入并继续其他资源；重跑可复用合法抽取缓存，但会重新执行所选投影。

每批拓扑提交或向量删旧写新并非跨库事务。网络提交结果可能未知，错误仍传播，不承诺回滚；维护入口记录失败资源并按同范围幂等重跑。无脚本内重试。

## 5. 退出码与完成核对

| 退出码 | 含义 |
|---|---|
| 0 | 报告无上述错误；全部跳过也可能是 0，需要检查 skipped |
| 1 | 初始化失败，或运行报告有模型 failed/资源错误；初始化失败可能没有 JSON 报告 |
| 2 | argparse 参数/资源文件/本体文件错误，在客户端创建前退出 |

正常完成查看 JSON 与日志中的资源 ID，区分复用、降级、跳过和已完成写入；只凭退出 0 不能断言所有目标都已修复。确认目标仍是预期 applied 身份，并从可信查询入口抽查可见正文/图证据。脚本没有 dry-run、在线调度或全历史回收功能。

参数/状态复核/部分失败的替身测试见 [context repair tests](../tests/scripts/test_repair_context.py) 和 [graph repair tests](../tests/scripts/test_repair_graph.py)。示例不代表已经在业务环境执行。

## 6. Kafka DLQ 手动重放

主消费者仅做进程内 1/2/4 秒快速重试，然后进入 `<main>.DLT`；ValueError 直接进入 DLQ。没有延迟 topic 或自动回投。未知程序错误也只有三次等待，实际处理时间还包含业务调用耗时。发送 DLQ 确认后才提交原分区 offset。

处理前用 Kafka 自带工具查看 `<main>.DLT` 各分区记录位置与错误 headers：rag-exception-type、rag-exception-message、rag-exception-traceback，定位并修复原因。最低运维要求是有人查看失败消息的积累；没有常驻 DLQ consumer group 时没有可直接使用的 consumer lag，需将 log end offset/消息量与人工已处理位置对照，不能把“topic 已创建”当监控已接入。

脚本只重放明确的 DLQ 分区和起始 offset，count 默认 1。以下示例从仓库根目录执行，broker/topic/位置换成已确认目标：

```powershell
.\.venv\Scripts\python.exe .\services\wisepen-rag-service\scripts\replay_dlq.py `
  --bootstrap-servers broker:9092 `
  --topic wisepen-document-ready-topic `
  --partition 0 --offset 42 --count 1
```

| 参数 | 语义 |
|---|---|
| --bootstrap-servers | 显式 broker 地址，不读取 Nacos |
| --topic | 原主 topic；读取来源为 `<topic>.DLT` |
| --partition、--offset | DLQ 分区与起始 offset，不是原主消息位置 |
| --count | 最多重放条数，必须为正数；默认 1 |
| --timeout-seconds | 单次读取/发送等待上限，默认 30 秒 |

启动时固定 DLQ end offset，超过该位置的新记录不纳入本次操作。按照 DLQ headers 的原主 topic/partition 回投，保留原始 bytes、key 与业务 headers，移除 rag- 诊断 headers。目标 topic 必须匹配 --topic；缺少位置 header 或发送失败直接退出。脚本不修改消息正文，因此坏 JSON/错误字段应由上游修复并重新生成，原样重放不会修复坏数据。

每条发送确认后打印 dlq_record_replayed，包含成功重放的 DLQ offset；完成打印 dlq_replay_completed。来源记录不删除，也不提交 DLQ offset。保存日志中的成功位置，失败后从未确认位置继续；发送超时可能实际已送达，重复由现有应用幂等承接。回投确认只代表进入主 topic，业务是否恢复应再检查主消费者结果、publication/ACL 或新 DLQ 记录。

旧延迟 topic 不再被运行时消费；升级前若有遗留消息，应人工查看、归档并按原始位置处理，本轮没有执行线上 topic 删除或清空。对应脚本测试见 [replay tests](../tests/scripts/test_replay_dlq.py)。
