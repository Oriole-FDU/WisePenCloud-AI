# RAG 失败策略 Review

状态：2026-10-14 B01–B07 已按用户“运维成本低”的评估结论实施或明确保持。A01–A14、接入拼写和上游 ACL 语义修复此前已获批准。文末 content=null 仍待上游合同评估。

## 评估口径

指导原则：①信任内部代码和框架；②只在系统边界校验；③不为不可能情况增加处理；④不吞错误（包括禁止宽泛 catch）；⑤优先快速失败，不掩盖问题。

不能仅凭 `except`、空结果或 fallback 判断冗余。需要区分：

- 合同允许的正常缺失，与合同损坏。
- 外部暂时故障，与内部程序错误。
- 有明确结果、日志和恢复路径的失败隔离，与错误被转换成成功。
- 当前操作快速失败，与整个服务必须退出。两者可以有不同失败单位。
- 代码中的明确设计、测试证明的现行行为，与本次 Review 对设计目的的推断。

设计意图能解释取舍，但不能自动证明捕获范围、可观测性和失败分类正确。原则④若按“绝对禁止宽泛 catch”解释，部分边界隔离实现确实冲突；若关注“不静默丢失错误”，带有明确失败结果的隔离不一定冲突。最终解释由人工评估确定。

## 已确认的处置

| 项 | 选择 | 约束 |
|---|---|---|
| B01 | 可选增强继续隔离，只捕获模型调用 | 不改 Common；不新增监控字段；不拆在线/repair 策略 |
| B02 | 损坏缓存 warning 后重算 | 正常指纹失效静默；已知校验使用专用异常 |
| B03 | 图种子作为附加能力隔离 | 只捕获图事实解析的专用校验异常；不新增结果字段 |
| B04 | 保留 GC 隔离和 ready，补错误指标 | 不限于 I/O；不加阈值或告警系统 |
| B05 | 1/2/4 秒进程内重试，然后 DLQ | 删除延迟层；ValueError 直接 DLQ；手动重放 |
| B06、Common 重排 | 共用语义，不改 | Nacos 注册、重排不是就绪必要条件 |
| B07 | 等待取消收尾并记录额外异常 | 忽略 CancelledError；不覆盖主错误或改变失败类型 |

## B01：可选上下文增强失败后继续摄入

代码：[Contextualizer](../src/rag/ingestion/transformers/contextualizer.py)。测试：[上下文](../tests/ingestion/test_contextualizer.py)、[发布](../tests/application/publication/test_vector_publication.py)。

**当前选择与设计意图**：上下文是可选检索增强。模型失败清除旧上下文、标记 failed 后继续，必需事实及投影失败仍阻止发布。try 只包 complete 调用，semaphore 获取与 tokenizer 不在其中。捕获 OpenAIError/OSError，以及 Common 用于空响应/截断的 ValueError；删除裸 RuntimeError。输出预算不合法使用 InvalidContextOutputError，与 tokenizer 程序错误分开。

**合理性**：原文能力不依赖可选模型；failed 状态、旧值清理和 warning 保留诊断，无需新增字段或基础设施。真实供应商 ValueError 仍在调用点被接住。

**保留的风险**：调用内部如果自身错误使用 ValueError，类型仍不能完全区分；收窄调用范围以最低成本控制风险，不改变 Common 的异常合同。在线与 repair 暂不分策略。

**原则关系**：模型是②的边界，失败真实可发生，①③不冲突。增强是失败单位，⑤不要求正文能力一起失败；④通过状态与日志保留错误。内部 RuntimeError/tokenizer 错误传播，避免掩盖程序问题。

## B02：损坏缓存失效后自动重算

代码：[Contextualizer](../src/rag/ingestion/transformers/contextualizer.py)、[GraphExtractor](../src/rag/ingestion/transformers/graph.py)、[图事实解析](../src/rag/core/schema/graph.py)。测试：[上下文](../tests/ingestion/test_contextualizer.py)、[图抽取](../tests/ingestion/test_graph_extractor.py)。

**当前选择与设计意图**：缓存是可丢弃的派生结果。指纹不符正常静默；指纹一致但结果无效，记录 invalid_cached_content warning，包含 chunk_id、cache_type、reason，然后重算。上下文使用 InvalidContextOutputError，图事实使用 GraphFactValidationError。图结构解析只将 Pydantic ValidationError 转为专用异常，本体/来源/重复事实校验明确抛同一类型；不再用通用 ValueError/TypeError 当缓存失效。

**合理性**：重算是实际恢复路径，严格完整缓存验证避免把剔除非法项后的部分结果当成功命中。校验器程序错误不被误认为缓存损坏。

**保留的风险**：warning 尚不等于已有告警；持续错误写入可能造成重算，但本轮不引入新系统。保留已有本体内容参与指纹，不新增本体版本字段，未提出新升级协议。

**原则关系**：②允许缓存边界验证。自动恢复与⑤并不矛盾；诊断恢复后保留，解决④的静默问题。正常失效不是异常，保持静默符合①③。

## B03：正文命中中的图种子损坏，不撤销正文结果

代码：[chunk_seed_entities](../src/rag/application/retrieval.py)。测试：[Chunk seeds](../tests/application/graph/test_chunk_seeds.py)。

**当前选择与设计意图**：正文优先于附加图信息。图字段完全缺失是正常空种子；图事实解析抛 GraphFactValidationError 时 warning 并返回空种子。try 只包解析调用；程序 TypeError、其他 ValueError 和结果组装异常传播，不新增结果字段。

**合理性**：图种子是后续探索的附加能力，不能撤销已授权的正文命中。与 A13 的区别：上下文是 Chunk 自身参与索引的字段，图种子是附加能力；前者不允许强制字符串化掩盖错误类型，后者可以在已知解析失败时隔离。

**保留的风险**：应用结果不能区分无图与坏图，诊断依赖现有日志。接受这一低成本取舍，不虚构无消费者的状态字段。

**原则关系**：②允许持久化 metadata 解析。④通过日志保留损坏原因；⑤按附加能力的失败单位解释，与正文继续可用并不矛盾。程序错误传播，避免把①扩展为自动修复程序错误。

## B04：GC 资源级隔离与周期恢复

代码：[GC](../src/rag/application/gc.py)。测试：[GC](../tests/application/test_gc.py)、[生命周期](../tests/application/test_lifespan.py)。

**当前选择与设计意图**：保留资源级 Exception 隔离、errors 报告和扫描失败后的周期恢复。error(..., exc=...) 已通过 Common 输出 traceback。新增 OTel counter rag.gc.errors，每个失败立即加 1，stage 仅取 vector/graph/acl/content/scan，不使用 resource_id 等高基数标签。不撤销 ready，不限定为 I/O，不加连续失败阈值。

**合理性**：永久销毁标记已撤销可见性，物理删除幂等，下轮可以收敛。GC 失败不破坏在线检索的授权正确性，进程退出不一定改善清理进度。复用现有 OTel 通道，不新建监控系统。

**保留的风险**：程序错误也被隔离，可能周期重复；指标暴露不代表告警已配置，等有监控消费者再决定告警。实际保留物理数据的时长由运维处理能力决定。

**原则关系**：②③不冲突。若④绝对禁止宽泛 catch，字面上仍有例外；用户已基于明确资源隔离设计选择保留。错误有 traceback、计数和指标，不是静默成功。⑤以当前资源/轮次为失败单位，不要求全服务取消 ready。

## B05：进程内快速重试、DLQ 与人工重放

代码：[KafkaConsumerClient](../src/rag/backends/kafka/consumer.py)、[手动脚本](../scripts/replay_dlq.py)。测试：[Kafka](../tests/backends/kafka/test_kafka_consumer.py)、[发布](../tests/application/publication/test_consumer.py)、[脚本](../tests/scripts/test_replay_dlq.py)。

**当前选择与设计意图**：删延迟 topic、relay、可用时间 header 和跨消息重试计数。默认首次处理加 1/2/4 秒三次重试，共四次业务执行；ValueError 直接 DLQ，其他异常包括未知程序错误仅快速重试，不设异常白名单或业务分类回调。ContentIncompleteError 继承 ValueError，也直接 DLQ，保留 staged 状态供处理，不再作为特例重试。

转发保留原始 bytes/key/业务 headers、原 topic/partition/offset，以及错误类型、消息和 traceback。先等 DLQ 转发确认再提交当前分区 offset；发送、确认和连接错误传播。手动 replay 显式选 DLQ partition/offset/count，按原主分区回投，不做自动回投，详情见[运维手册](operations.md)。

**合理性**：只保留无需额外基础设施的瞬时抖动恢复。相对十几分钟自动链，未知程序错误在三次等待共 7 秒后进入人工失败处理；实际耗时还包括每次业务调用。删除延迟层降低 topic、权限、保留期和投递诊断的运维成本。

**保留的风险**：未知错误仍会重复执行，且若 DLQ 无人观察和处理，就只是存下错误。最低要求是 Kafka 工具能查看 DLQ 各分区消息量/位置并对照已处理记录；没有常驻 DLQ consumer group 时不能把不存在的 consumer lag 当已配置监控。本轮不新增告警系统。默认调用可能耗时较长，max_poll_interval 仍需覆盖四次业务调用和等待。

**原则关系**：真实网络抖动符合②③。带错误诊断的 DLQ 是明确失败交付，④不是静默丢弃，但宽泛 handler 隔离是用户批准的例外。⑤以有限消息处理为失败单位，删除延迟层使失败更快到达人工处理。broker 未确认时不提交输入，符合快速暴露基础设施故障。

## B06 与 Common 重排：共用语义，不改

代码：[lifespan](../src/rag/main.py)、[Common Nacos](../../wisepen-common/src/common/cloud/nacos_client.py)、[Common RetrievalPipeline](../../wisepen-common/src/common/utils/retrieval/pipeline.py)。

**已确认选择**：Nacos 注册失败仍允许就绪，注销 best effort；Common 重排失败记录后保留召回顺序。这是多个微服务共用的语义，注册与重排不属于就绪必要条件。RAG 不单边改，共用语义，不改。

**合理性与原则关系**：日志保留故障，可用的主能力继续服务；⑤按发现/排序附加能力而非整服务解释。宽泛捕获的④字面例外已按共享设计接受，不作为本轮继续重构的入口。

**保留的风险**：注册可用性和重排质量仍需运维观察；Common 注销日志不携带异常对象的已有问题保留。本轮选择不修改 Common 日志或公共异常合同，避免扩大范围。

## B07：取消任务后等待并记录额外异常

代码：[PublicationEventConsumer](../src/rag/application/publication/publish.py)、[GraphExtractor](../src/rag/ingestion/transformers/graph.py)。测试：[发布](../tests/application/publication/test_consumer.py)、[图抽取](../tests/ingestion/test_graph_extractor.py)。

**当前选择与设计意图**：保留 gather(return_exceptions=True)，收尾之后逐项检查结果；正常返回与 CancelledError 忽略，其余 BaseException 使用 error(..., exc=...) 输出 traceback 和任务名或块 ID。既不覆盖主错误，也不聚合为新的异常类型。Kafka 删延迟层后直接 await 单一消费者，不再保留没有兄弟任务的编排。

**合理性**：等待收尾防止遗留 I/O 和提前关闭连接；取消是正常生命周期结果。额外真实失败不再被丢弃，同时保留调用方依赖的原始异常合同。

**保留的风险**：主错误也可能再次出现在收尾日志中；接受少量重复日志，不加去重状态或额外异常协调器。

**原则关系**：①②③⑤不冲突；处理的是实际任务生命周期。④的非取消异常诊断丢失已解决，预期取消无需当故障记录。

## 上游核对：合法缺省与静默忽略

核对来源：用户指定的 `D:/WisePenCloud-AI/Backup/backend/WisePenCloud` Java 仓库；只读核对，没有修改该仓库。以下结论是源码合同证据，不代表已检查真实 Mongo 数据或线上发布配置。

| 字段/行为 | 上游证据 | RAG 本轮处理 |
|---|---|---|
| specifiedUsersGrantedActionsMask 顶层缺失/null | ResourceItemEntity 不初始化；ResourceServiceImpl 改权限时明确 set null；assembler 无指定值时走群组规则 | 保留空指定用户 ACL 的合法语义，错误非对象类型失败 |
| computedGroupAcls 顶层缺失/null | ResourceItemEntity 不初始化；重算前可无值；assembler 显式检查 null | 保留尚无群组投影的语义；不虚构已完成重算事件 |
| 已生成群组的 baseMask/userMasks | ComputedGroupAcl 默认 0/空 Map；calculateResourceGroupAcl 使用 new 并写入两个字段，消费者按整型 mask 和 Map 使用 | 已有组内缺项/null/错误类型拒绝；未观察到现行生产路径主动省略它们 |
| triggerSource | AclRecalculateMessage 注释声明可选，仅供日志追踪 | 允许缺省；事件 extra=ignore 不属于吞业务错误 |
| DocumentReadyMessage type/metadata | Java 消息只有 resourceId/version/content | 保留 type=None 和空 metadata；要求上游必填扩展字段会破坏当前生产合同 |
| ResourceDeletedMessage typedResourceIds | publisher 对非空资源集合按类型分组，未知类型归 UNKNOWN | 保留开放类型分组与展开所有资源；不凭空筛掉未知资源类型 |

源码定位：

- ResourceItemEntity：`wisepen-resource-service/wisepen-resource-biz/src/main/java/com/oriole/wisepen/resource/domain/entity/ResourceItemEntity.java:21`。
- ComputedGroupAcl：`wisepen-resource-service/wisepen-resource-biz/src/main/java/com/oriole/wisepen/resource/domain/ComputedGroupAcl.java:17`。
- ResourceServiceImpl 权限写入：`wisepen-resource-service/wisepen-resource-biz/src/main/java/com/oriole/wisepen/resource/service/impl/ResourceServiceImpl.java:432`、群组重算：`wisepen-resource-service/wisepen-resource-biz/src/main/java/com/oriole/wisepen/resource/service/impl/ResourceServiceImpl.java:825`。
- ResourceItemResponseAssembler 判权：`wisepen-resource-service/wisepen-resource-biz/src/main/java/com/oriole/wisepen/resource/service/assembler/ResourceItemResponseAssembler.java:253`。
- AclRecalculateMessage：`wisepen-resource-service/wisepen-resource-api/src/main/java/com/oriole/wisepen/resource/domain/mq/AclRecalculateMessage.java`。
- DocumentReadyMessage：`wisepen-document-service/wisepen-document-api/src/main/java/com/oriole/wisepen/document/api/domain/mq/DocumentReadyMessage.java`。
- KafkaResourceEventPublisherImpl：`wisepen-resource-service/wisepen-resource-biz/src/main/java/com/oriole/wisepen/resource/mq/KafkaResourceEventPublisherImpl.java`。

### 已确认并修正：接入名称的拼写错误

Java ResourceItemEntity 声明 `wisepen_resource_items`，Java MqTopicConstants 的 ACL topic 是 `wisepen-resource-acl-recalc-topic`。RAG 此前的集合、ACL topic 和消费者 group 前缀少了字母 e。用户随后明确要求统一替换，已修正源码默认值、配置样例、测试及文档，全部使用 wisepen 前缀。

这一项是来源合同拼写修正，不是失败策略变更。集合错误可能被“记录不存在”正常路径吸收，topic 错误可能导致收不到事件；不是必要防御。实际 Nacos 配置与已有 Kafka group offset 不由本地替换自动迁移；已有自定义 group 需沿用实际配置或由部署方评估重放。

### 已获授权并修正：忠实投影上游 VIEW 判权

用户明确要求 RAG ACL 语义忠实于上游，已修正 `_read_group_acls` 丢弃默认可读群组用户排除项的问题。用户 ID 和群组 ID 保留上游原值，不靠 trim 改写身份。

参照 ResourceItemResponseAssembler.resolveAccess 的预计算 ACL 判权，资源所有者优先，资源级指定用户 mask 覆盖全部群组授权；群组 OWNER/ADMIN 拥有 VIEW；普通成员使用 userMasks.getOrDefault(user, baseMask)，多群组按位或。群组默认可读时，用户 mask=0 仍须排除；默认不可读时，用户 VIEW mask 可以单独授权。MARKET_VIEW（bit 9）不被当作 VIEW（bit 1）。

上游预计算快速判权还有市场组目标版本限制：管理员先于版本限制，普通成员必须匹配 offerVersion。RAG 现将 groupBinds 中的 offerVersion 保存为 GroupResourceAcl.market_offer_version；快照用实际 applied document_version 判权，避免用旧售卖授权访问新版本。字段有真实生产者与消费者，不是未来预留元数据。

这是已授权的权限语义修复，不属于 B01–B07 的失败策略决策。RAG 仍使用事件触发的本地预计算 ACL，未改为上游在线逐标签计算，不声称消除了既有最终一致性窗口。已有本地群组 ACL 记录须重新同步以补齐 market_offer_version；缺少新字段时直接读取失败，不能把旧市场投影当普通群组自动放行。

### 补充待评估：DocumentReady content=null

DocumentServiceImpl 的一个发布路径通过 findById(...).map(getSearchText).orElse(null) 生成 readyContent；另一路直接调用 getSearchText。RAG 当前 content:str 拒绝 null，由消息失败策略处理。

生产者确实能发 null，但这不足以证明“null 等于合法空文档”。空字符串是正文事实，缺少已就绪内容可能是发布链路问题。当前保持拒绝，不用 `content or ""` 修复；需上游确认 READY 的内容合同及重试是否能恢复。

## 已实施 A 项与本文边界

A01/A02 信任客户端解析和数量保证；A03/A04/A05 删除已保证的重复检查；A06 只删除内部数值重复验证，保留能力组合合同；A07 经 Common 保证复核后删除 Mongo/repair 重复结构与章节检查，读取仅解码，内部增强只建索引；A08 信任来源生成及唯一索引；A09 在 ObjectId 输入边界统一身份并信任 Mongo 范围；A10 直接访问 ready；A11 区分上游合法 null 与非法条目；A12 信任权威解析与 writer，直接还原本地完整 ACL 投影；A13 删除上下文强制字符串化；A14 序列化错误传播。

内部重复校验及其人工损坏测试删除，真实接入合同、模型解码和 I/O 失败测试保留。B01–B07 已按上面的独立业务评估实施；测试验证执行合同，不能替代后续实际运维消费。

## Mongo 内部生成事实的校验收敛

按 Common 算法及生产写入链路复核：章节父引用、无环、跨度嵌套、正文范围、章节分块连续性由 Common 构建保证；total_length 由 RAG 直接取同一正文长度。Mongo 只做原有模型解码，不重新遍历章节树或扫描章节连续性。repair 也不重复检查由同一生成链建立的 chunk 章节引用。发布的身份、缺失与预期 chunk 集合回查仍保留，检查真实 I/O 完整性。

本地 ACL 已经过权威解析，由本仓储保存完整字段；读取直接还原，不再使用逐字段严格 TypeAdapter，也不做 str/bool/int 容错转换。旧投影仍须重同步补齐 market_offer_version，缺字段不自动放宽市场授权。

contextual_context 由 Contextualizer 的正常生成覆盖或失败/预算回退清除；不为上游 metadata 偶然携带同名非字符串字段增加校验、保留字段协议或回退。未运行 Contextualizer 时不承诺清理这个字段。已有指纹一致但缓存无效的 warning 与重算策略继续保留。
