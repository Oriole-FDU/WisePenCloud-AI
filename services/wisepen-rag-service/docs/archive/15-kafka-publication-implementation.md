# RAG Kafka、事件发布闭环与外部 Pipeline 实施记录

## 模块边界

Kafka 位于 `rag.backends.kafka`，供 ACL 与 publication 共用。`consumer.py` 管理 Kafka 连接、快速重试、延迟回投、DLQ、当前分区
offset 提交及取消清理；`retry.py` 声明 RetryPolicy、RetryTopic 和 DeliveryHeader。后端不导入 ACL、publication、Pipeline、Mongo
或任何业务模型。`common.kafka` 本轮改动已经还原，最终没有 Common 源码或测试变更。

`application.publication/publish.py` 中的事件定义校验上游 DocumentReadyMessage 的 resourceId/version/content，以及
ResourceDeletedMessage 的 typedResourceIds。内容事件复用 Common MarkdownChunker 的公共 API 构造 DocumentStructure 和
canonical ContentRevision；不改写正文、不把 Kafka headers 放进 Document metadata。Common 目前以分块结果暴露章节和锚点，所以入口提取结构会先调用
Common，后续外部 Pipeline 的 MarkdownChunker 再执行正式分块。

publication 收敛为 `models.py`、`store.py`、`publish.py` 三个实现文件，保留 `__init__.py` 做包导出。`publish.py`
同时定义事件、PublicationEventConsumer 和 DocumentPublication。消费者只注入 publisher：内容调用 publish，物理销毁调用
destroy_resources，所有资源类型都建立永久销毁标记。publish 内部完成身份核对、stage、Document 保存、外部完整 Pipeline、事实回查和条件
apply；状态端口继续原子判断版本、冲突及销毁状态。独立 DocumentIngestion 与公开 stage/apply
入口已删除，不保留旧文件或兼容别名；无需在消费者中组装摄入步骤，也不增加 Pipeline 装配校验。ACL 权限判断继续属于
ACL，版本与销毁准入不引入新的 ACL 校验。

## 默认重试合同

| 阶段                    | 已执行的重试次数 | 失败后的动作            |
|-------------------------|------------------|-------------------------|
| 首次主 topic 执行       | 0                | 等待 1 秒               |
| 第一次快速重试          | 1                | 等待 2 秒               |
| 第二次快速重试          | 2                | 等待 4 秒               |
| 第三次快速重试          | 3                | 转发 `<main>-retry-1m`  |
| 1 分钟到期回投主 topic  | 4                | 转发 `<main>-retry-10m` |
| 10 分钟到期回投主 topic | 5                | 转发 `<main>.DLT`       |

最多五次重试、六次业务执行；该预算属于一条正常重试链。进程崩溃或 offset 提交失败后的重新投递仍可能重复业务执行，不承诺全局
exactly-once 或跨数据库事务。

Publication 的坏 JSON、字段错误、ContentIdentityError、ContentConflictError 直接进入 DLQ。ContentIncompleteError 可以重试；I/O
和运行时失败消耗预算。ACL 的字段与权威数据错误（ValueError 系列）直接进入 DLQ，I/O 失败走同一重试链。异常分类位于用例，通用
Kafka 后端只调用分类函数。

延迟 topic 不执行 handler：按 `rag-available-at-ms` 等待，到期后将原始 value、key 和 headers 送回原主 topic 的原分区，再确认
retry topic 当前记录。每个延迟 topic 有自己的消费循环，等待不会阻塞主 topic；该 retry
队列自身逐条等待，不承诺按资源并行调度。业务始终在主分区串行执行，新内容可先于旧重试应用，旧内容由版本合同返回
STALE。销毁独立于内容消费，可在构建期间撤销候选。

## 确认、失败与可追踪信息

成功或明确跳过后提交 `{TopicPartition: offset + 1}`。预算耗尽或永久失败时先等待 retry/DLQ 的 send_and_wait
成功，再提交输入位置。producer 使用 acks=all，发送确认与驱动请求预算默认 30 秒，可通过 delivery_timeout_ms 配置。未开启
producer idempotence：该模式不会按普通请求超时过期批次，可能在 broker 故障时无限等待，当前有界退出依靠普通发送和应用幂等。发送与
offset 提交不在 Kafka 事务内，二者间故障或发送超时后仍送达都允许重复转发。发送、确认超时或提交失败直接传播，取消其余循环并关闭全部
consumer/producer，交由运行入口重启；不把 broker 故障再次转发到同一个故障 broker。

上述超时及幂等模式行为依据 [aiokafka Producer 文档](https://aiokafka.readthedocs.io/en/stable/producer.html)
，分区提交使用 [官方 Consumer API](https://aiokafka.readthedocs.io/en/latest/api.html)。延迟 deadline 的输入校验与 poll
预算共用 30 秒余量，允许有限时钟偏差而拒绝外部无限扩大等待期限。

value、key 和非保留 headers 均保留。`rag-` 传输 headers 包含：

- original-topic、original-partition、original-offset：首次输入位置；
- retry-count：正常链已执行／下一次延迟回投的重试次数；
- available-at-ms：延迟到期时间，仅在 retry 记录上存在；
- exception-type、exception-message、exception-traceback：最近失败；文本分别限制 4096／8192 字符，防止无限 header 增长。

保留原始消息而不构造新的业务 envelope 或 Stored 模型。DLQ 不被本消费者自动订阅，也没有自动重放接口；修复数据或下游后，由运维显式决定重放，并处理传输保留
headers 和重试预算。

## 运行入口与部署要求

AclEventConsumer 已迁移到此后端，并通过可等待的 run () 进入 FastAPI TaskGroup。启动握手等待 producer 和主 consumer
连接；消费基础设施失败向生命周期传播。健康检查在启动未完成或生命周期退出后返回 503，正常期间返回 200；不会让一个已经停止的
ACL 消费循环继续显示健康。main 的运行入口不自动重启，重启策略由部署入口决定。

修复 ACL Container 的旧 Mongo import，并补齐它既有的 dependency-injector 依赖；uv.lock 只增加 RAG 对该已锁定依赖的引用。新增
KAFKA_MAX_POLL_INTERVAL_MS，默认 300000；主循环预算必须覆盖整条业务执行和快速重试。每个 relay 的预算至少是它的延迟加 30 秒。

部署应为每个 main topic 配置主 topic、两个 retry topic 和 `.DLT`，具备对应读写及 consumer group 权限。业务代码不负责 topic
管理；测试用随机隔离 topic。retry 保留期须覆盖延迟和故障积压，DLQ 使用能保留 null 原消息的 delete 清理策略，保留期按排障需要设置。原上游须继续以
resourceId 作为内容 key，RAG 回投显式指定原主分区。

publication 仍以显式依赖启动，尚未注册到全局 Container/Nacos：真实 VectorIndexer 尚未实现，不能用 fake
或空必需投影把生产发布接起来。完整装配为：

```python
from rag.application.publication import (
    DocumentPublication, PublicationEventConsumer,
)
from rag.ingestion.pipeline import IngestionPipeline
from rag.ingestion.indexers import ChunkFactsIndexer

pipeline = IngestionPipeline(
    chunker=chunker,
    transformers=transformers,
    indexers=[ChunkFactsIndexer(writer=content), *required_projection_indexers],
)
publisher = DocumentPublication(
    store=states,
    reader=content,
    writer=content,
    pipeline=pipeline,
)
consumer = PublicationEventConsumer(
    publisher=publisher,
    bootstrap_servers=bootstrap_servers,
    group_id="wisepen-rag-publication-group",
    max_poll_interval_ms=max_poll_interval_ms,
)
await consumer.run()
```

## 验证

本地测试覆盖完整五次重试、1/2/4 秒退避、快速恢复、主队列不被延迟阻塞、原 key／分区回投、原 value 和非保留 headers、坏消息与冲突直接
DLQ、事实不完整重试、旧重试被新 applied 淘汰、转发失败不确认、提交失败允许已转发记录保留、启动失败、取消及生命周期健康状态。fake
仅替代 I/O，实际用例与传输编排仍执行生产代码。

真实 Mongo 测试沿用 RAG_TEST_MONGODB_URI；三个真实 Kafka 测试使用 RAG_TEST_KAFKA_BOOTSTRAP_SERVERS，随机隔离主／retry／DLQ
topic，验证恢复、销毁与完整延迟回投/DLQ headers。未配置则跳过；配置后连接失败会报错。Kafka 集成中的短延迟只用于缩短测试，生产默认仍为
1 分钟和 10 分钟。

最终检查：服务 pytest 为 202 passed、14 skipped（11 Mongo、3 Kafka）；服务源码与测试 Ruff check、修改文件 Ruff format
--check、compileall、git diff --check 与新增文件空白检查均通过。core.schema 独立导入不加载
application/ingestion/storage/backends；Common 没有最终 diff。上述测试通过不代表真实 broker 与 Mongo 已验证，本环境没有配置两个测试连接。

真实 VectorStorage/VectorIndexer、publication 全局生产装配、物理清理、回收站可见性和可选图谱仍待后续实现。
