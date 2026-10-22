# 内容身份、Application Publication 与普通摄入实施记录

## 已完成能力与归属

ContentRevision 位于 core/schema/content.py，使用 frozen、slots dataclass 表达 resource_id、document_version、完整 content_sha256。create 根据原始 Document.text 的 UTF-8 字节计算身份，保留空白和 Unicode 原始形式；content_revision 为 `resource_id@document_version#完整SHA-256`。现有 Document、DocumentStructure、Chunk 字段不变。

publication 位于 application/publication，是资源变更到可检索状态的应用用例，不作为与 ACL 并列的独立领域能力。当前收敛为三个实现文件：models.py 定义流程状态、结果枚举和错误；store.py 定义状态端口；publish.py 定义事件、消费者及完整发布操作，__init__.py 仅负责包导出。独立摄入用例已合并进 DocumentPublication.publish，消费者仅注入 publisher。Kafka 接入与重试增量见 15 实施记录。publication 当前仍采用显式依赖，不自动注入全局 Container/Nacos。

ResourcePublicationState 只有 resource_id、两个完整 ContentRevision（staged_revision/applied_revision）、destroyed。MongoPublicationStore 显式绑定 resource_publications，通过 TypeAdapter 保存和恢复嵌套对象；显式 ensure_indexes 创建 resource_id 唯一索引。没有 Stored 镜像模型、Repository 层、构建租约、完成清单或跨库事务。

## 执行合同

DocumentPublication.publish 接收已构造 Document，注入状态 Store、内容 Reader/Writer 及外部组装的 IngestionPipeline，不分别接收 Chunker、Transformer 或 Indexer。内容身份、版本、冲突和销毁判断均在执行 Pipeline 前完成；原子状态判断与条件更新仍归 Store。调用方负责同资源构建串行以及完整 Pipeline 配置，事实 Indexer 必须在全部必需投影之前。执行顺序固定为：

```text
核对 Document.text 与 content_revision
  -> 条件 stage
  -> ContentWriter 保存 Document
  -> 普通 Pipeline：Chunker -> Transformers -> ChunkFactsIndexer -> 必需投影 Indexers
  -> 返回最终同一 Chunk 列表
  -> 回查 Document 身份与精确有序的预期 Chunk ID 集合
  -> 条件 apply，清空 staged
```

Pipeline 只增加最终 list[Chunk] 返回值，不增加组件识别、空配置检查、装配校验、依赖图或深拷贝。Indexers 只读共享输入，只有全部必需写入确认后才能成功。投影完整性依赖这一成功合同，不增加向量查询接口；fake 投影只在测试中存在，生产调用方须提供真实必需投影。

stage 先判断 destroyed，再淘汰低于 staged/applied 最高版本的提交，再拒绝同最高版本不同完整摘要。已 applied 的同内容提交返回 ALREADY_APPLIED，不重写 metadata；同内容 staged 重放允许重建；更高版本替换 staged 并保留旧 applied。摄入入口返回 APPLIED、ALREADY_APPLIED、STALE 或 DESTROYED；真正内容冲突、内容不完整与 I/O 失败抛异常。

回查 Document 的资源、版本、正文摘要及 revision 字符串；读取该 revision 的全部 Chunk，按 chunk_index 排序后的 ID 列表必须与本次最终列表完全一致。缺失、多余和顺序不符均阻止 apply。不重新分块或逐字段比较增强 metadata。零 Chunk 内容允许发布；Common 当前把仅标题原文生成一个 flat Chunk，也正常发布，不强加“只有标题必须零 Chunk”的假设。

stage 固定读取到的两个指针及销毁状态进行条件更新；首次插入竞争唯一索引后只重新读取分类。apply 只更新仍为候选的未销毁资源，失效后分类为旧版本、已销毁、已发布或相应错误。仍无法完成的竞争抛 PublicationStateChangedError；组件不自动重试。构建、回查或发布异常保留已完成事实和投影，旧 applied 不被撤销。

## 物理销毁与上游边界

destroy_resources 去重，每批 500 个有序 UpdateOne upsert，按资源原子地置 destroyed=True 并清空 staged/applied。未摄入资源也建立标记，永久保留；后续任何版本不能恢复同 ID。部分成功保留、异常传播，不删除内容或投影。

本操作只对应后端 ResourceDeletedMessage 和 `wisepen-resource-physical-destroy-topic`。后端源码确认三个不同阶段：个人 .Trash 保留业务主档并剥离共享权限；softRemoveResources 将主档移到 RESOURCE_TRASH_COLLECTION；hardRemoveResources 从回收集合清理后才发布物理销毁事件。回收站与软删除不调用永久销毁标记。所有者仍可能通过 ACL，所以回收站搜索可见性另列上游 issue，本轮不处理。

Kafka 内容／销毁事件、确认、有限快速重试、延迟回投和 DLQ 已在 15 增量实现；物理清理、请求内 selected revision 读取、真实 VectorStorage/VectorIndexer 和可选图谱尚未实现。Kafka 使用 RAG 内独立后端，common.kafka 保持原状。

## 显式装配

```python
from rag.application.publication import DocumentPublication
from rag.backends.mongo import MongoContentStorage, MongoPublicationStore
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.pipeline import IngestionPipeline

content = MongoContentStorage(
    documents=database["document_revisions"],
    chunks=database["doc_chunks"],
)
states = MongoPublicationStore(database["resource_publications"])
await content.ensure_indexes()
await states.ensure_indexes()

pipeline = IngestionPipeline(
    chunker=MarkdownChunker(),
    transformers=transformers,
    indexers=[ChunkFactsIndexer(writer=content), *required_projection_indexers],
)
publisher = DocumentPublication(
    store=states,
    reader=content,
    writer=content,
    pipeline=pipeline,
)
action = await publisher.publish(document)
```

database、已构造的 canonical Document、Transformer 列表和全部真实必需投影由应用入口提供。示例不启动服务，不用空配置或 fake Indexer 作为生产发布捷径。

## 验证与后续

publication 基础阶段的测试结果为 125 passed、11 skipped，基线 63 个本地测试继续通过，新增 62 个本地用例覆盖身份、状态、竞争窗口、事实回查、固定执行顺序及失败保留。新增 8 个真实 Mongo 用例与原有 3 个用例都因未配置 RAG_TEST_MONGODB_URI 跳过；这不代表真实 Mongo 条件更新、唯一索引或竞争行为已验证。Kafka 增量后的当前结果见 15。

从仓库根目录完成以下检查：

| 检查 | 结果 |
| --- | --- |
| 服务 pytest | 125 passed、11 skipped |
| 服务 Ruff check | 通过 |
| 本轮 Python 文件 Ruff format --check | 通过 |
| 服务源码与测试 compileall | 通过 |
| git diff --check 与新增文件行尾空白检查 | 通过 |
| 独立导入 core.schema，不加载 application、ingestion、storage 或 backends | 通过 |

真实集成测试可显式配置测试 URI 后运行 tests/backends/mongo/test_publication_integration.py，每个 fixture 只创建和清理自己的 codex_rag_publication_test_<随机ID> 数据库；配置存在而连接失败时测试失败，不转成跳过。

内容／物理销毁消费者及确认与重试已经接入；下一阶段接真实 VectorStorage 和 VectorIndexer，装配完整生产 Pipeline 并接入运行入口，随后补物理清理与可选图谱。实际消费者调用本用例，不另写一套 stage、保存或 apply 逻辑。
