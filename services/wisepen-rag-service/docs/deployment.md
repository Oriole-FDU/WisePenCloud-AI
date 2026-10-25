# 运行配置、启动与验证

正常入口是 [rag.main](../src/rag/main.py)，装配见 [Container](../src/rag/container.py)，流程见[架构图](architecture.md)。离线
repair 使用自己的配置，详见[运维手册](operations.md)。本文描述代码要求与启动方法，不代表已完成真实部署验收。

## 1. 环境与入口

服务目录和发行包名为 `wisepen-rag-service`，Python ≥3.11，依赖定义见 [pyproject.toml](../pyproject.toml)。根项目
dependencies 和 tool.uv.sources 已纳入此 workspace 成员，普通根项目同步会安装它；只准备 RAG 环境时，从仓库根目录执行：

```powershell
uv sync --locked --package wisepen-rag-service
```

Python 模块名保持 rag，确认 `rag.__file__` 指向 services/wisepen-rag-service/src/rag。目录迁移后重新同步 editable
安装；若环境残留旧发行包 wisepen-rag，先用 `uv pip uninstall wisepen-rag` 移除其旧路径登记，再同步新服务。

## 2. bootstrap 环境

由 [RagBootstrapSettings](../src/rag/core/config/bootstrap_settings.py)
与 [Common BootstrapSettings](../../wisepen-common/src/common/core/config/bootstrap_settings.py) 在拉取 Nacos
前读取环境/.env。

| 配置                             | 默认/要求                                 |
|----------------------------------|-------------------------------------------|
| NACOS_SERVER_ADDR                | 必填                                      |
| NACOS_NAMESPACE_ID / NACOS_GROUP | 空字符串 / DEFAULT_GROUP                  |
| NACOS_USERNAME / NACOS_PASSWORD  | 可选                                      |
| NACOS_REGISTER_IP                | 可选；多网卡/容器可显式指定注册地址       |
| PROFILE                          | dev，可选 prod                            |
| SERVICE_HOST / SERVICE_PORT      | 127.0.0.1 / 19906；容器监听按部署修改     |
| SERVICE_NAME / APP_NAME          | wisepen-rag-service / WisePen RAG Service |
| LOG_LEVEL                        | INFO                                      |

Nacos data ID 为 `{SERVICE_NAME}-{PROFILE}.yaml`，默认开发环境为 wisepen-rag-service-dev.yaml。bootstrap 环境用于定位配置；Nacos
YAML 是 AppSettings 内容，不是 repair 环境变量。

## 3. AppSettings 与外部依赖

[app_settings.py](../src/rag/core/config/app_settings.py) 正常导入会拉取
Nacos；失败终止启动。配置样例见 [platform-settings.yaml](platform-settings.yaml)，地址、凭据和模型名须替换。

| 配置组    | 必填与默认行为                                                                                      |
|-----------|-----------------------------------------------------------------------------------------------------|
| Mongo     | MONGODB_URL、MONGODB_DB_NAME 必填；RESOURCE_MONGODB_DB_NAME 未设回落主库                            |
| Qdrant    | QDRANT_URL 必填，QDRANT_API_KEY 可选；三套集合名必须非空且互异                                      |
| Embedding | EMBEDDING_BASE_URL/API_KEY/MODEL/DIMENSIONS 全部必填，dimensions 为正整数，没有默认维度             |
| 精排      | QWEN_API_KEY、QWEN_RERANKER_MODEL 必填；平台固定 Qwen provider，无 rerank 开关                      |
| 上下文    | ENABLE_CONTEXTUALIZE=false；启用或任一 INSTRUCTION_BASE_URL/API_KEY/MODEL 非空时要求三项齐全        |
| Neo4j     | ENABLE_GRAPH_STORAGE=false；启用要求 NEO4J_URI/USERNAME/PASSWORD，DATABASE=neo4j、GRAPH_NAME=rag_v4 |
| GC        | GC_INTERVAL_SECONDS=600（有限正数）、GC_BATCH_SIZE=100（正整数）；始终运行                          |
| Kafka     | KAFKA_ENABLED=false；启用要求非空 KAFKA_BOOTSTRAP_SERVERS；MAX_POLL_INTERVAL_MS=300000              |
| 扩展      | EXTENSIONS=[]；源码表名称显式选择；Employment 要求 FROM_SOURCE_SECRET、完整指令及 Neo4j             |

ENABLE_GRAPH_STORAGE 只装配图后端，不把 GraphExtractor 加进默认链。默认 pipeline 根据 ENABLE_CONTEXTUALIZE
惰性选择带/不带上下文链，扩展可显式选择。Employment 当前选择不带上下文链。

### 物理空间

| 存储                                                              | 当前名称/用途              |
|-------------------------------------------------------------------|----------------------------|
| Mongo document_revisions / doc_chunks                             | 完整文档与 Chunk 事实      |
| Mongo resource_publications / resource_acls                       | 发布状态与本地权限         |
| 上游资源库 wisepen_resource_items                                 | ACL 权威读取，注意实际拼写 |
| VECTOR_COLLECTION=document_chunk_vectors_v4                       | 普通 dense/sparse 命名向量 |
| GRAPH_ENTITY_VECTOR_COLLECTION=graph_entity_source_vectors_v4     | dense-only 实体来源向量    |
| GRAPH_RELATION_VECTOR_COLLECTION=graph_relation_source_vectors_v4 | dense-only 关系来源向量    |
| Neo4j NEO4J_GRAPH_NAME=rag_v4                                     | 拓扑图元与独立来源贡献     |

Qdrant 普通 sparse 固定服务端 qdrant/bm25 multilingual，使用 cloud_inference=True；部署需提供实际推理支持。仅启动探活/集合初始化不能证明模型推理、BM25
或真实业务链可用。集合配置冲突启动失败，不自动删除/迁移旧数据。

### Kafka 默认命名

| 项目              | 默认值                                  |
|-------------------|-----------------------------------------|
| ACL topic         | wisepen-resource-acl-recalc-topic       |
| ACL group         | wisepen-rag-v4-acl-recalc-group         |
| 内容 topic        | wisepen-document-ready-topic            |
| 物理销毁 topic    | wisepen-resource-physical-destroy-topic |
| publication group | wisepen-rag-v4-publication-group        |

ACL/publication group 必须不同。为每个主 topic 配置 `<main>.DLT` 的创建、权限与保留期；只有进程内 1/2/4 秒快速重试，不创建延迟重试
topic。内容 key 使用 resourceId，同资源发布串行；重试/确认语义见[发布文档](usecases-publication.md)。

## 4. 启动、健康检查与关闭

把 Nacos 对应 YAML 准备好后，从仓库根运行：

```powershell
$env:NACOS_SERVER_ADDR = '<nacos-host:port>'
$env:PROFILE = 'dev'
.venv/Scripts/python.exe -m rag.main
```

main 登记共享客户端，构造扩展并保存 app.state.extensions，探活 Mongo、初始化索引和全部 VectorCollection、可选 Neo4j
schema，挂载扩展 API，构造必要模型组件与默认链。启动不向真实模型发送探测请求。

GC 首轮立即启动；Kafka 开启时等待所有主消费者真正连接。Nacos 注册失败记录但不阻止 ready，首轮 GC 不参与等待。查询可在关闭
Kafka 时使用，不过内容摄入和 ACL 更新需可信维护入口或已有数据。

健康接口为 GET `/rag/health`，未就绪返回 503，ready 返回 200，body 有 status/service。根应用 `/docs` 是当前 FastAPI
文档，通用业务查询 HTTP API 尚未挂载；Employment 子 API 的独立文档需要可信网关 Header。

```powershell
Invoke-RestMethod http://127.0.0.1:19906/rag/health
```

后台任务退出视为异常，撤销宿主/子应用 ready；关闭先取消并等待任务，再逆序关闭共享客户端一次，最后尝试 Nacos
注销。扩展不另建连接关闭生命周期。

## 5. 常见问题

| 现象                              | 核对方向                                                                               |
|-----------------------------------|----------------------------------------------------------------------------------------|
| 导入 app_settings/main 就失败     | bootstrap 是否齐全、Nacos data ID/namespace/group 是否对应、AppSettings 必填项是否完整 |
| 关闭上下文仍提示 instruction 配置 | 检查是否配置了部分 INSTRUCTION_*，或扩展是否实际需要 GraphExtractor                    |
| 开启图存储后普通文件没有图        | 默认链无图阶段，需注册含 GraphExtractor/GraphIndexer 的扩展                            |
| Employment 构造失败               | 完整 INSTRUCTION_*、ENABLE_GRAPH_STORAGE/连接、FROM_SOURCE_SECRET 和集合隔离           |
| 查询空或数量不足                  | applied/ACL、旧投影身份、过滤预算及图 fact_sha256；不补召回是现行合同                  |
| Kafka 关闭但仍出现删除日志        | GC 始终运行，只扫描永久 destroyed 标记；不是 broker 消费                               |
| 已发布事件重放没有修复索引        | 同 revision 已发布跳过；使用明确维护入口，见 operations/employment                     |
| 图本体条件查不到旧来源            | 重建缺 ontology_name 的拓扑或过期图向量，见 operations                                 |

## 6. 本地验证与外部验收边界

文档/组合测试可用替身，不启动 Nacos/main。工作区验证命令：

```powershell
.venv/Scripts/python.exe -m pytest -c services/wisepen-rag-service/pyproject.toml `
    services/wisepen-rag-service/tests/test_extensions.py services/wisepen-rag-service/tests/test_container.py `
    --basetemp=.pytest_cache/rag-docs -q
git diff --check
```

按改动范围增加定向测试；纯文档迁移无需重复全业务回归。真实后端 fixture 仅在显式配置时用随机隔离空间，缺配置跳过，连接失败报错：Mongo
`RAG_TEST_MONGODB_URI`、Qdrant `RAG_TEST_QDRANT_URL`（可选 RAG_TEST_QDRANT_API_KEY）、Kafka
`RAG_TEST_KAFKA_BOOTSTRAP_SERVERS`、Neo4j `RAG_TEST_NEO4J_URI` 及该 fixture
所需凭据，具体见[测试目录](../tests/conftest.py)。

单元/替身通过与真实部署、模型效果验收分别记录。不要以业务消费者、业务 GC/repair 或业务集合代替隔离 fixture。
