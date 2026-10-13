# P5 首片：Ontology、增量图抽取与离线 repair

记录日期：2026-10-12。实现前 HEAD 为 8310a94a；本片是未提交工作区改动。当前排期不做确定性 Producer，插件装配按实际消费者再确定。

## 实现合同

- core/schema/graph.py：GraphNode、GraphEdge、Ontology、EntitySpec、RelationSpec 均为标准 dataclass。Ontology.from_file 支持 JSON/YAML/YML、UTF-8 BOM，文件边界检查类型、未知字段、键值一致和端点类别声明，内部可直接构造；domain 映射 ontology_name，空端点限制仍表示不限制该侧类别。
- ingestion/transformers/graph.py：GraphExtractor 和仅用于 Instructor 的 Pydantic 输出 DTO，DTO 全部字段有 description。图事实和本体采用字段旁注释；缓存字典在边界使用 TypeAdapter 解析，不增加镜像事实模型。
- scripts/repair_graph.py：显式资源范围与本体 JSON/YAML，当前 applied 身份核对、写前完整状态复核、全部 Chunk 保存及资源级报告。维护窗口同资源写入串行；不 stage/apply、不重分块、不写普通向量。
- [装配和命令](../demo/graph.md)、[本体示例](../demo/graph-ontology.json)。事实使用 ChunkFactsIndexer/ContentWriter 完整替换保存，读回 metadata 仍是普通字典。

材料固定为当前 raw_text + section_path；标题只辅助消歧。模型输出名称、类别和有向关系，程序计算稳定 ID 和当前 Chunk 来源。节点 source_id 与关系 source_ids 是来源，关系 source_id/target_id 是端点。

Instructor 校验响应结构并使用自身重试；未知本体值、空白名称、缺失合法端点和错误方向按事实粒度丢弃。同块 ID 去重，首次合法事实保留，不跨 Chunk 聚合。模型/解析异常取消并等待其他任务，所有块成功后才替换图 metadata；失败不执行事实写入，数据库旧事实保留。

补漏默认关闭，开启最多一轮；提示词要求不修改/重复正确事实、无增量返回空列表。程序也禁止同 ID 覆盖首轮字段，新关系可引用首轮实体。首轮超预算抛错，补漏历史超预算记录日志并保留首轮。默认输入 8000 token、每轮输出 4096 token、实例并发 4；预算包含消息和 schema 文本，不等于供应商封装开销。

成功保存 entities、relationships、graph_version。指纹覆盖策略、模型、tokenizer、完整本体、schema、生成参数和实际消息；不含并发、来源 ID、生成上下文或无关 metadata。复用前校验缓存模型、本体和来源，合法空结果可复用。reuse_existing=False / repair --force 强制重算。

repair 资源失败继续其他资源，有错非零退出；部分数据库写入保留，重跑只复用已持久化的有效块。图字段不改变 retrieval_text，因此不重写普通向量。

## Reference 与实际验证

定向核对 LightRAG commit 453dce83d6d0354a06e46c8d4029a0895c4e054b 的 operate.py / prompt.py：当前块、可选标题背景、一次可选补漏及历史预算跳过。本片重复 ID 保留首次合法事实，不采用 reference 的较长描述替换策略。未引入其 runtime，未声称完整算法 golden 对齐。

- RAG：606 passed / 18 skipped。跳过为 12 Mongo（新增图事实往返）、3 Kafka、3 Qdrant，未配置真实连接。
- Common Markdown / AI clients / retrieval：125 passed。Chat 无本片消费方改动，未重跑其历史 19 项回归。
- 真实 InstructionClient + Instructor + OpenAI HTTP 本地替身覆盖字段 schema、缺类别后重试、可选补漏及增量复用，没有调用真实模型。
- 覆盖来源、方向、非法事实、BSON 往返、指纹/损坏缓存、跨调用并发、异常与取消收尾，以及 repair 状态变化和 500 条批边界部分写入后重跑。
- 修改文件 Ruff check/format 通过；全目录 Ruff check 存在既有 contextualizer.py 导入排序 I001，HEAD 原文件同样复现，未修改该无关文件。
- compileall、git diff --check 与锁文件 offline check 通过。图事实/本体模块无 ingestion、application、storage 或具体 backend 依赖，Extractor 不导入 backend/配置，repair 不加载 Nacos 或向量客户端。
- 当前 .venv 缺 pytest/Ruff 和新 rag 包，通过 uv pip 补齐工具并以 --no-deps 安装 editable services/rag。YAML 加载使用已有 PyYAML 6.0.3，RAG 显式声明 pyyaml>=6,<7；uv lock --offline 仅增加该包的两条依赖关系。

未对业务数据库运行 repair，未验证真实模型、Mongo/Kafka/Qdrant、图拓扑或生产生命周期。下一片讨论 GraphStorage/GraphIndexer 的多来源聚合、资源/revision 投影与失败重建。
