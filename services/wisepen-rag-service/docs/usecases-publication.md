# 发布、事件消费与永久销毁

用于把上游完整 Markdown 变成可查询的当前版本。入口是 [DocumentPublication / PublicationEventConsumer](../src/rag/application/publication/publish.py)。
发布状态、动作与状态异常见 [domain/models/publication.py](../src/rag/domain/models/publication.py)；ContentIdentityError 和
ContentIncompleteError 仍属于 [application/publication.py](../src/rag/application/publication/publish.py)。存储端口见
[domain/storage/publication.py](../src/rag/domain/storage/publication.py)。身份及组件合同分别见[模型](models.md)、[抽象](abstractions.md)。

## 1. 依赖与最小调用

DocumentPublication 注入 PublicationStore、ContentReader、ContentWriter、默认 IngestionPipeline 和可选
`pipelines: dict[str, IngestionPipeline]`。平台只拥有一个 publisher；注册字典由 type 选择完整链，扩展不替换消费者。

```python
from rag.application.publication import DocumentReadyEvent


async def publish_markdown(publisher):
    event = DocumentReadyEvent.model_validate({
        "resourceId": "resource-example", "version": 1,
        "content": "# 项目说明\n\n当前正文。", "metadata": {"topic": "示例"},
    })
    return await publisher.publish(event.to_document())
```

这是可信组合入口示例，调用前初始化存储，并保证同资源构建串行。没有通用 publish HTTP API，也不从内容事件授予 ACL。

## 2. 输入合同

| 输入                              | 字段与默认行为                                                                                    |
|-----------------------------------|---------------------------------------------------------------------------------------------------|
| DocumentReadyEvent                | resourceId 非空字符串、version 严格整数、content 原始字符串；type=None、metadata={}；忽略额外字段 |
| ResourceDestroyedEvent            | typedResourceIds 按类型分组的非空资源 ID 列表；展开全部类型，不只处理文档                         |
| `publish(document)`               | Document.content_revision 必须符合完整正文身份；type 精确匹配注册键，未知类别写入前失败           |
| `destroy_resources(resource_ids)` | 永久销毁，不表示物理清理已经完成；读侧从新快照起不可见                                            |

to_document 用 Common 解析结构并映射章节 ID，Pipeline 再正式分块；当前存在这次重复解析。原文空白保留。type/metadata
不参与身份，变更摄入效果需要提高 version；本体或分块配置变化另行维护。

## 3. 状态、顺序与结果

[`ResourcePublicationState`](../src/rag/domain/models/publication.py) 保存 resource_id、staged_revision、applied_revision、destroyed。staged 是构建候选，applied
是新请求可见版本，destroyed 是永久标记。

```text
身份核对与选链 → 条件 stage → 保存 Document → 完整 Pipeline
→ 回查 Document 身份及精确有序的预期 Chunk ID 列表 → 条件 apply
```

stage 优先检查销毁，再比较 staged/applied 最高版本；低版本跳过，同最高版本不同摘要冲突，已 applied 相同身份跳过，同身份
staged 允许重建。更高版本可替换 staged，旧 applied 保留。

| [PublicationAction](../src/rag/domain/models/publication.py) | 对外含义                                    |
|-------------------|---------------------------------------------|
| APPLIED           | 本次事实回查及条件发布完成                  |
| ALREADY_APPLIED   | 已发布相同身份，未重建                      |
| STALE             | 旧版本被更高版本淘汰                        |
| DESTROYED         | 永久销毁阻止提交                            |
| STAGED            | 内部继续构建的结果，不是 publish 的完成结果 |

允许零 Chunk，但正常 Common 分块可能把纯标题文档生成 flat Chunk。回查列表缺失、多余、乱序均拒绝发布；必需向量成功由 Indexer
确认，不额外查询向量完整性。

## 4. 失败与恢复

| 情况                          | 行为                                                                 |
|-------------------------------|----------------------------------------------------------------------|
| 身份错误、未知 type           | stage/事实写入前失败                                                 |
| 同最高版本内容冲突            | ContentConflictError；不能通过重试把冲突变成合法版本                 |
| Transformer/必需 Indexer 失败 | 停止后续阶段，不 apply；保留旧 applied、staged 及已完成写入          |
| 回查事实不完整                | ContentIncompleteError；作为 ValueError 直接 DLQ，处理原因后人工重放 |
| 条件 apply 期间状态被取代     | Store 重读分类；不能确定终态时抛 PublicationStateChangedError        |

Contextualizer 可以按组件合同降级后成功返回；注册链内 GraphExtractor/GraphIndexer 未处理异常仍阻止发布。独立图 repair
失败不撤销已有 applied，这与在线发布不同。

已发布的同身份重放不会修复投影；同身份 staged 重放可以恢复未完成构建。更换分块配置后旧 Chunk/旧投影清理未纳入本用例，没有跨库回滚或在线重构建。

## 5. Kafka 消费合同

默认内容 topic 为 `wisepen-document-ready-topic`，物理销毁 topic 为 `wisepen-resource-physical-destroy-topic`；默认
publication group 为 `wisepen-rag-v4-publication-group`。内容事件以 resourceId 作 Kafka key，由调用链保证同资源串行；销毁独立消费，不依赖跨
topic 顺序。

RAG 使用自身 [Kafka 客户端](../src/rag/core/kafka.py)：进程内快速重试 3 次，等待 1/2/4 秒，仍失败进入
`<main>.DLT`。正常处理最多 4 次业务执行；崩溃/重复投递时不能承诺总次数或 exactly-once。

`PublicationEventConsumer` 注入 `KafkaConsumerClient` 实例并把各自的业务 handler 传给 `run(handler=..., started=...)`。Container
构造 Kafka 客户端时提供 bootstrap servers、topic、group 和 retry policy；实现见
[core/kafka.py](../src/rag/core/kafka.py)。此职责拆分不改变下述重试、DLQ 确认和 offset 提交语义。

坏 JSON/字段、身份错误、版本冲突和其他 ValueError（包括 ContentIncompleteError）直接 DLQ；其他 handler 异常只快速重试，不加异常白名单。发送
DLQ 确认后才提交输入 offset+1；发送或提交失败退出，由入口重启。保留 key、业务 headers 和原始
payload，附带原位置与错误类型/消息/traceback，不写重试计数。无 Kafka 事务，无延迟层或自动 DLQ
回投；人工重放见[运维手册](operations.md)。

消费 run 必须 await 并观察异常，主消费者真正连接后才 ready。topic
创建、权限及保留期由部署配置负责，详见[运行说明](deployment.md)。

## 6. 永久销毁注意事项

destroy_resources 会为尚未摄入的资源也建立标记，清空
staged/applied，永不恢复或删除标记。迟到写入不能恢复发布，物理残留由[GC](usecases-gc.md)回收。

上游个人回收站、softRemove 与 hardRemove 是不同阶段，只有物理销毁事件调用此入口。回收站搜索可见性不是目前 ACL
已解决的能力，不要用永久标记实现可恢复删除。
