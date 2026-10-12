# P8 第二片实施记录：Document 类型路由与业务 metadata 透传

日期：2026-10-13。HEAD 为 `f7d76b21`；P8 首片及本片仍为未提交改动。保留首片装配/GC、用户 retrievers 目录调整和相关代码/测试改动，以及已有子模块状态。本片只修改 RAG，不修改上游生产者、Common/Chat 源码、依赖或锁文件。

## 实现与接口

- Document 在既有 metadata 默认字段之后增加 `type: str | None = None`，保持既有位置参数兼容。摄入类别与资源物理类型、Ontology domain 分开，开放字符串按原值精确匹配。
- DocumentReadyEvent 接收可选同名 type 与默认空字典 metadata，沿用 strict 外部字段类型校验和 extra=ignore；to_document 透传两者，不读取其他未声明字段。
- MongoContentStorage 复用现有 Document TypeAdapter 和完整替换合同，不新增集合/Stored 模型或迁移；旧记录缺失 type 时读为 None。
- 默认 MarkdownChunker 深复制 Document.metadata 到每个 Chunk，嵌套值不与文档或其他块共享。type 只在 Document 保存，不增加 Chunk.type 或影子 metadata，不自动投影任意业务字段。

DocumentPublication 保留默认 pipeline，增加可选 `pipelines: dict[str, IngestionPipeline]`；保存传入映射本身，包括空字典。publish 正文身份校验后选定本次链，再执行现有 stage/Document/Pipeline/回查/apply：None 用默认链，显式未知类型在任何写入前抛出 ValueError，本次调用在 await 后不重新读取路由。

Container 增加 `pipeline_routes = providers.Singleton(dict)` 并注入 publication。每个容器实例独立持有字典，构造 publisher 前后注册均可见；正常在启动消费者前执行：

```python
container.pipeline_routes()["history"] = history_pipeline
```

普通字典允许显式替换同名键；不增加 Registry、冻结、重复拒绝、在线热更新或管理接口。垂类只组装 Pipeline 并注册，平台仍拥有唯一 publication 和 Kafka 消费者。未知类型沿用已有 Kafka ValueError 永久错误/DLQ 合同，消费者生产代码不为路由增加分支。

## 失败与版本

注册链内所有阶段均属必需阶段，GraphIndexer 在链内时其失败也阻止 apply。已完成事实/普通向量/图写入保留，旧 applied 不变，不回滚或补偿。希望独立降级的图阶段继续由发布后的独立流程拥有，本片不增加调度。

ContentRevision 不包含 type、metadata 或 Pipeline 配置。影响摄入的 type/metadata 变化要求提高上游 version；相同已发布 revision 继续返回 ALREADY_APPLIED，不改写旧 metadata。注册或替换路由不自动重建历史事实。旧版本和永久销毁合同保持，重建通过显式维护流程执行。

## 文档与开发示例

更新 demo/extensions.md：子容器仅构造垂类 Pipeline，外层入口赋值类别键；移除以覆盖整个 publisher 接入垂类的主示例。示例明确注册链中 GraphIndexer 为必需阶段，与可选发布后图流程分开。增加未来事件 JSON、字段透传、版本行为、实例内映射和启动注册说明；实际执行子容器/注册/依赖替身代码，使用真实平台工厂验证本体参数与默认链保留。

同步 demo/platform.md、设计 11、排期 10 和交接 16，修正旧“Document metadata 不复制到 Chunk”描述。普通向量与图投影不自动包含 type/年代等业务字段，扩展仍须显式投影和权威回查。

## 实际验证

| 范围 | 结果 |
| --- | --- |
| 全 publication、Markdown 适配、Mongo 内容、Container、文档示例与核心 schema 定向 | **169 passed / 3 skipped** |
| RAG 全服务 | **883 passed / 28 skipped** |
| Common retrieval、ai_clients、markdown | **125 passed** |
| 本片 12 个 Python 文件 Ruff check/format、compileall | 通过 |
| git diff --check | 通过 |

定向使用 `services/rag/tests/application/publication`、`tests/ingestion/test_markdown.py`、`tests/backends/mongo/test_content.py`、`tests/test_container.py`、`tests/test_extensions.py` 和 `tests/core/test_schema.py`，basetemp 为工作区 `.pytest_cache/p8-type-target-final`。随后 RAG 全服务使用 `.pytest_cache/p8-type-full`；Common 加 `--asyncio-mode=auto`，使用 `.pytest_cache/p8-type-common`。

覆盖旧事件/Document 兼容、字段类型、开放类别与精确匹配、多个路由和默认链、未知类别无 stage/写入、身份检查先于路由、构造前后注册、容器隔离、调用内固定选择、同 revision 跳过/新版本路由/旧版本/销毁、嵌套 metadata 互不污染、BSON 往返，以及真实 VectorIndexer/GraphIndexer 在注册链内的失败保留。

消费者替身证明类型事件由现有平台 consumer 路由、成功确认和未知类型永久 DLQ，无业务消费者连接。真实后端 fixture 未显式配置，定向三项 Kafka 和全服务 28 项真实后端测试跳过；未调用真实模型、运行 repair 或清理业务库。本片没有新建真实后端 fixture 或扩大清理范围。Windows Ruff 通过 stdin 写回，仅格式化本片文件，保留用户其他改动。

## 后续边界

上游以后提供 type/metadata 即可使用现有入口，本片没有修改生产者协议实现。没有动态配置、自动发现、插件 Runtime、统一 Ontology 目录或发布后阶段调度。GC、来源授权、现有查询 filters 和旧 revision 回收边界不变；按业务 metadata 检索仍需要垂类自己的显式投影与准备阶段。
