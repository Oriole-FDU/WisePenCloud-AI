# 当前实施约定（2026-10-29）

本节更新覆盖下文历史阶段中的检索、离线同步和凭证约定。

- 本地检索采用 BM25 dis_max + SPECTER2 + RRF60 + 有界引用加分；title/TLDR/abstract=3/2/1，取消 `2<70%`
  。实现与参数见 [混合检索](hybrid-search.md)。
- 模型合同并入 embedding_runtime，检索规则内联在仓储及 arXiv provider；召回规模、RRF k、引用加分权重和 CPU 线程通过
  AppSettings/Nacos 控制。离线 SQL 输入指纹继续负责向量续跑复用和增量失效判断。
- Scholar main 一次启动 HTTP 与 CPU 查询编码，使用工作区依赖；NACOS_ENABLED 可关闭 Nacos。没有额外启动入口。
- CS 检索来源为 `[arxiv, huggingface]`。通用富化链保持 arxiv → s2 → openalex；CS 的领域增量为 HF。
- OpenAlex/HF 搜索和 HF 发现仅使用用户 BYOK，缺 key 时在缓存读取前禁用来源；无可用来源返回 503。lookup/富化仍允许平台 key，用户 key 优先。S2 支持 BYOK，用户 FIFO 按 key 隔离且不设排队阈值，平台跨进程合流走 NOKEY/KEY 双通道，离线与在线 KEY 共享节流且在线优先。
- 搜索候选缓存区分日期、领域与模型版本，用户 limit 只裁剪返回结果。search/discover 以 candidates-v2 保存投影前候选；完整论文 lookup 缓存版本不变，仍按来源和稳定身份共享。论文事实缓存跨凭证共享，但搜索和发现必须先通过 BYOK 可用性判断；在途 I/O 按有效 key 隔离。
- lookup 统一返回基础论文和调研 metadata，删除 purpose、重复的 item.metadata 及 enrichment_status；metadata 只出现在 paper 内。HF 可独立提供基础论文。
- 各 provider 实现信号、别名、TTL 及字段投影，由 providers/policies.py 中的 ProviderPolicy 注册层统一组装。search、lookup、discover 最终出口统一投影；具体字段合同见 [README](../README.md#模型可见-metadata)。
- 本次 GPU FP16 只负责存量灌库。后续每日 arXiv OAI + S2 Graph batch，必须请求 `embedding.specter_v2`；不使用 S2 diff，不下载
  release 大包。离线代码移出 src，位于 deploy/offline。
- 分期交付：HF 搜索；查询编码与同进程启动；存量向量持久化及每日 API 同步；混合检索与版本索引切换。每期维护稳定接口，向量不进入
  Paper。
- 验收包含顺序回退、跨 BYOK/平台及无凭证缓存复用、limit 复用、v2 向量持久化/重放、增量保留向量及全量 alias 切换。真实 62
  篇集成验收已通过；全量运行结果以迁移日志为准。

操作入口及每日维护见 [deploy/README.md](../deploy/README.md)。V2 学习排序见 [TODO](TODO.md)。

此前批量 lookup 历史验收（旧 purpose 合同）：Scholar 全量 158 项通过、6 项显式集成测试跳过；MCP Scholar 9 项通过，改动路径 Ruff（Python 3.11）与
`git diff --check` 通过。全量测试使用进程内测试配置关闭 Nacos，避免配置中心鉴权影响测试收集；未修改运行配置。HTTP
合同通过正式路由及真实 Provider 转换、可控上游响应验证 HF 独立兜底、三种 purpose 共用完整缓存及失败记录。6 项跳过分别为真实
ES/GPU 验收和 5 项真实 Redis 队列验收；本轮不宣称完成这些真实服务集成或生产部署。

---

# WisePen Scholar V1 开发计划

> 本文是需求、边界与分期验收的唯一计划。各阶段只交付当期能力，通过验收后独立提交；不以空实现、临时 API 或未来字段补齐阶段外能力。

## 1. 目标与服务职责

Scholar 提供论文检索、精确查询和趋势发现，供 Agent 组合完成学术调研。

| 能力或数据                | 归属与职责                                                                        |
|---------------------------|-----------------------------------------------------------------------------------|
| Scholar                   | 拥有来源路由、论文关联、检索、富化及数据同步，提供内部 HTTP API                   |
| MCP 服务                  | 注册三个工具，读取用户工具配置，向 Scholar 传递请求和必要凭证；不复制学术业务逻辑 |
| Chat 服务                 | 沿用现有工具目录、用户秘密配置及 MCP `_meta` 通道                                 |
| arXiv 本地镜像            | 提供基础论文事实及本地召回，覆盖范围以实际 arXiv 收录为准                         |
| OpenAlex                  | 使用用户 BYOK 或平台配置提供跨学科学术检索及精确查询                              |
| S2                        | 提供通用学术信号；离线数据集、在线精确查询和每日同步各有明确调用边界              |
| Hugging Face Papers（HF） | 提供 Computer Science 搜索、专用富化和趋势发现，使用异步 HTTP API                 |

V1 不建设全文语料库、完整引用图、作者研究工具、用户密钥管理系统或研究问题拆解流程。不预生成经典、热门、高影响力等学术判断标签，也不把不同来源的指标相加。

### 1.1 不得混淆的富化入口

| 入口                       | 通用富化                                                                    | 领域增量                                        |
|----------------------------|-----------------------------------------------------------------------------|-------------------------------------------------|
| search                     | 本次成功 searcher 包含 arxiv/s2/openalex 时直接消费检索数据，否则执行通用链 | 全部执行，排除本次成功执行的同源 searcher       |
| lookup details/enrich/full | 每篇依次 arxiv→s2→openalex，先读完整来源缓存，获得可用信号即停止            | 按请求 field 全部执行；无基础信息时可由 HF 提供 |
| discover                   | 直接交付渠道列表，不逐篇执行通用链                                          | 保留渠道实际返回的数据                          |
| 每日同步                   | 保留独立离线补充规则和持久进度                                              | 不执行在线领域增量                              |

领域由请求配置选择，不从结果内容推断。初始仅 CS 的 field_enrichers 注册 HF，其余为空。通用链未解决论文按来源批量回退，0/false
是有效信号；arXiv 只有基础论文信息不算富化成功。

### 1.2 工程架构约定

沿用 Chat/Sandbox 的典型微服务结构：`main.py` 管理日志、观测、生命周期与注册；`container.py` 装配依赖；`api` 提供薄 HTTP 接入；
`application` 按用例编排；`core/storage` 实现存储读模型；`etl` 独立执行离线任务。

`domain/models/` 中的 `paper.py`、`identifiers.py` 只保存论文和身份领域语义。请求、结果、来源执行状态等细节放在具体应用用例或其共享执行模型中，不放入一个总
models.py。

`application/` 只有 search、lookup、discover 三个用例，分别由 `search.py`、`lookup.py`、`discovery.py` 编排。
`lookup_runner.py` 是 search/lookup 共用的内部执行机制，负责通用短路、领域增量、缓存及在途请求复用，不是额外应用服务。供应商信号与身份规则通过注册绑定注入，留在
`providers/`。

`domain/models/fields.py` 定义学术领域和策略模型，完整策略来自 Nacos YAML 的 `SCHOLAR_POLICIES`。
`core/config/policy_settings.py` 负责加载与热更新，application 通过 domain 的策略读取契约固定单次请求快照。`FieldPolicy` 只记录有序 `searchers`、领域增量
`field_enrichers` 和 `discoverer`；容器注册来源名称到 provider 能力的绑定。新增医疗来源时，兼容的 PubMed/WoS
searcher、富化器及容器注册可以由领域配置选择，无需在核心检索、缓存或融合流程增加供应商分支。`Paper.source` 接受开放字符串，来源专有事实放在
`metadata[来源名]` 自由字典中。

`providers/field_mapping.py` 仍是通用领域到 arXiv/OpenAlex 参数的统一映射文件。`providers/` 承载在线能力适配：本地 arXiv
搜索由 `ArxivProvider` 调用论文仓储，OpenAlex 实现同一 `PaperSearcher` 能力；HF 使用原生异步 HTTP，由 CS 搜索后处理和
lookup field 配置调用。arXiv OAI、存量文件导入和每日 S2 API 处理属于独立 `deploy/offline/` 步骤。

`domain/interfaces/` 按业务能力声明 `PaperSearcher.search()`、`PaperLookupProvider.lookup(ids)`、
`PaperDiscoverer.discover()`，不按供应商名称声明端口。一个 provider 可实现一个或多个能力。已知外部 I/O 由 provider 转为
`ProviderError`，保留原异常文本和 cause，不附带 reason/status 分类；程序错误和取消继续传播。S2 在线与离线调用共用 `core` 中的
HTTP 传输和 Redis 发送配额，不将离线方法放入 provider。

## 2. 领域映射与检索路由

### 2.1 V1 领域映射

下表是领域分类映射的唯一来源。OpenAlex 默认按 `topics.field.id` 过滤，多个 ID 使用 `|` 表示同字段 OR；法学使用明确列出的
subfield。arXiv 多分类按 OR 查询。

这些是不同分类体系之间的召回映射，不宣称一一精确等价。S2 不承担关键词召回，因此不维护未被消费的 S2 `fieldsOfStudy` 搜索参数。

| 请求值                             | 领域                                 | OpenAlex 过滤值           | arXiv 分类                                                                                                   | 有 BYOK 时默认来源与覆盖说明               |
|------------------------------------|--------------------------------------|---------------------------|--------------------------------------------------------------------------------------------------------------|--------------------------------------------|
| `general`                          | 跨学科、通识、未定领域               | 不过滤                    | 全部分类，不过滤                                                                                             | OpenAlex；无密钥时仅代表整个本地 arXiv 库  |
| `computer_science`                 | 计算机、AI、软件工程、数据科学       | `17`                      | `cs.*`                                                                                                       | arXiv；按 CS 配置执行 HF 富化              |
| `medicine`                         | 临床、基础医学、药学、护理、口腔     | `27,29,30,35,36`（OR）    | 无对应分类                                                                                                   | OpenAlex；无密钥时不支持该领域召回         |
| `biology`                          | 生物、遗传、神经科学、农学生命       | `11,13,24,28,34`（OR）    | `q-bio.*`                                                                                                    | OpenAlex；arXiv 仅覆盖定量生物学等相关方向 |
| `engineering`                      | 机械、电气电子、土木、化工、能源控制 | `15,21,22`（OR）          | `eess.*`                                                                                                     | OpenAlex；arXiv 主要覆盖电气工程与系统科学 |
| `economics_business`               | 经济、金融、管理、工商会计           | `14,20`（OR）             | `econ.*,q-fin.*`                                                                                             | OpenAlex；arXiv 覆盖经济与定量金融相关方向 |
| `mathematics`                      | 纯数、应用数学、统计、运筹           | `26,18`（OR）             | `math.*,stat.*`                                                                                              | arXiv                                      |
| `physics`                          | 物理、天文及相关方向                 | `31`                      | `physics.*,astro-ph.*,cond-mat.*,gr-qc,hep-ex,hep-lat,hep-ph,hep-th,math-ph,nlin.*,nucl-ex,nucl-th,quant-ph` | arXiv                                      |
| `chemistry`                        | 有机、无机、物理化学、分析化学       | `16`                      | `physics.chem-ph`                                                                                            | OpenAlex；arXiv 主要覆盖化学物理           |
| `psychology`                       | 认知、应用心理、脑与认知科学         | `32`                      | 无对应分类                                                                                                   | OpenAlex；不强制映射到其他 arXiv 分类      |
| `social_sciences`                  | 社会、政治、公共管理、传播、教育     | `33`                      | `physics.soc-ph`                                                                                             | OpenAlex；arXiv 主要覆盖社会物理与计算方向 |
| `arts_humanities`                  | 文学、历史、哲学、艺术、语言学       | `12`                      | 无对应分类                                                                                                   | OpenAlex                                   |
| `law`                              | 法学理论、民商、国际法、刑法         | `topics.subfield.id:3308` | 无对应分类                                                                                                   | OpenAlex；不静默扩大到整个社会科学         |
| `materials_environmental_sciences` | 材料、地学、环境生态、气象地理       | `19,23,25`（OR）          | `cond-mat.mtrl-sci,physics.ao-ph,physics.geo-ph`                                                             | OpenAlex；arXiv 仅覆盖对应交叉物理方向     |

### 2.2 有序检索与多源执行

`FieldPolicy.searchers` 是唯一检索名单，顺序即优先级；不再定义无凭证或独立多源计划。
`default` 依次调用来源，只在 ProviderError 时回退；正常空列表也算成功并结束。
`multi_source` 调用所有注册来源，保留成功结果和失败执行记录；所有来源失败时抛原始异常。
程序错误与取消直接传播。缺 OpenAlex key 属于普通来源失败，不提前拒绝整个计划。

CS 为 `[arxiv, huggingface]`；数学、物理为 `[arxiv, openalex]`；其他已有双源领域为 `[openalex, arxiv]`；其余领域为
`[openalex]`。
返回的 execution 仅记录实际尝试的来源、缓存命中及结果。单源保留原排序，多源沿用精确身份归并与等权 RRF。

### 2.3 本次调整的独立提交

1. 检索策略：已提交 `b632f02f`；有序回退、多源实际执行和领域配置收敛，26 项相关测试通过。
2. 富化编排：已提交 `5e92fcc9`，Scholar 165 项、MCP Scholar 9 项测试通过。已接通通用短路链、领域增量、按实际成功 searcher
   去重、OpenAlex 精选投影和 lookup field。保留身份冲突、缓存与取消合同；凭证缓存隔离已由当前共享约定取代。临床增量以替身 provider 验证，未接入真实临床来源。
3. S2 队列：已实现 Redis FIFO、Nacos 准入阈值和 queued_ahead。真实 Redis 独立命名空间验收 5 项通过，覆盖两个客户端 FIFO/1
   RPS、5/6 阈值、离线准入、取消/超时、过期租约、拥堵回退和缓存/在途合并。

领域 `field_enrichers` 只登记增量来源。平台不默认融合同名字段、不做语义去重，provider 负责输出键唯一并避免重复通用事实。
本次不操作后台离线任务，不部署生产。最终验收另覆盖 OpenAlex 批次取消、已有缓存信号保留、ES 外部失败回退和 full 基础论文保留时的
partial 语义。

最终验收：Scholar 全部 173 项通过（包括显式连接现有 Redis、使用独立随机命名空间的 5 项队列验收）；MCP Scholar 9
项通过；本次路径的仓库根目录 Ruff 检查和 git diff --check 通过。S2/OpenAlex/HF 的此次编排行为以可控 HTTP 边界测试验证；真实
Redis 验收不代表完成生产部署或重启了运行中任务。

## 3. 三个工具与 HTTP 契约

业务接口为 `POST /scholar/papers/search`、`POST /scholar/papers/lookup`、`POST /scholar/papers/discover`，使用仓库现有
`R<T>` 响应。就绪接口为 `GET /scholar/health`，实际依赖未就绪时返回 503。

API/MCP 边界负责解析请求，封闭值集合使用枚举。内部按 search、lookup、discovery 用例分别编排，不创建一个持有全部依赖的总服务。

### 3.1 `search_papers`

| 参数               | 类型与默认值             | 语义                                                         |
|--------------------|--------------------------|--------------------------------------------------------------|
| `query`            | 必填非空字符串           | Agent 已经明确的研究问题或检索关键词；不在工具内拆解研究问题 |
| `field`            | 领域枚举，默认 `general` | 选择请求配置，不根据召回结果推断                             |
| `published_after`  | 可选日期                 | 包含边界的起始日期                                           |
| `published_before` | 可选日期                 | 包含边界的结束日期                                           |
| `limit`            | 默认 10，范围 1–20       | 最终返回数量                                                 |
| `source_scope`     | 默认 `default`           | 执行默认或已注册多源计划                                     |

V1 不增加 `recency` 别名。时间过滤使用来源提供的发表或首次提交日期，结果保留其来源；不拿元数据更新时间代替发表时间。

本地召回采用 BM25 dis_max 与 SPECTER2 两路，各取 200，RRF k=60 融合，再按有界引用信号加分。title/TLDR/abstract 权重为
3/2/1，原字段与 `.en` 在逻辑字段内取较高分；OR 召回、完整标题加 8、标题短语加 4。引用数与高影响引用数的平滑加分合计不超过
30%，不使用年份。参数、缓存与编码流程见 [混合检索](hybrid-search.md)。

明确请求 CS 时，在候选归并并截取最终数量后执行 HF 富化。优先缓存，仅对有 arXiv ID 的候选请求 HF 论文详情，并发数默认
4，受本次请求预算约束。HF 使用 `httpx.AsyncClient`，凭证优先取 BYOK，否则取 Nacos `HF_TOKEN`，不使用同步 SDK 或 `to_thread`
。保留 AI 摘要、关键词、摘要模型、点赞、代码仓库、关联模型和数据集的实际返回值；不抓取整个关联仓库目录，不假定返回条目穷尽所有产物。404
表示 HF 没有对应记录，其他已知外部 I/O 错误保留原文本并标记可选后处理未完成；均保留基础论文结果。程序错误和取消继续传播。

### 3.2 `lookup_papers`

| 参数 | 类型与默认值 | 语义 |
|---|---|---|
| `identifiers` | 必填字符串列表，1–20 项 | DOI、arXiv、OpenAlex、S2、Corpus ID 或相应官方 URL |
| `field` | 领域枚举，默认 general | 选择领域增量来源 |

响应为 `items[{identifier,status,paper,error}]` 和 `execution`。找到的 Paper 包含基础信息及唯一一份 metadata，不接受 purpose。

1. 通用链固定 arxiv→s2→openalex，各来源先批量读缓存，未命中才访问 provider。每篇取得有效通用信号即停止；只有基础论文仍继续补充信号。
2. 未找到、不支持、外部失败或拥堵允许下一来源；歧义不任取候选，不继续关联。保留输入顺序和重复项。
3. 通用链后执行 field.field_enrichers。已有基础信息取首个成功来源，metadata 按来源命名空间保留；HF 也可独立提供 Paper。
4. 内部保留完整作者及来源事实，最终返回前统一投影。部分成功保留论文和真实错误；没有可用结果且发生查询失败时传播原始错误。
5. 来源缓存和在途请求按稳定身份共享；缓存命中不要求上游 key，未命中使用 BYOK 或平台凭证。只有确认未找到才负缓存；最后一个等待者取消才取消共享请求。

### 3.3 `discover_papers`

| 参数     | 类型与默认值                                 | 语义                                 |
|----------|----------------------------------------------|--------------------------------------|
| `field`  | 必填发现领域枚举，V1 只有 `computer_science` | 仅暴露已注册的发现能力               |
| `period` | `daily`、`weekly`、`monthly`，默认 `daily`   | 渠道日期或自然周期，不是滚动 7/30 天 |
| `sort`   | `recent`、`trending`，默认 `trending`        | 与周期参数一起按官方协议传递         |
| `limit`  | 默认 10，范围 1–15                           | 最终返回数量                         |

删除 `query`、`all_time`、`popular`。发现以渠道分发信号为入口，不执行主题搜索。

使用 HF 官方论文 HTTP
API，并遵循[官方 HfApi 源码](https://raw.githubusercontent.com/huggingface/huggingface_hub/main/src/huggingface_hub/hf_api.py)
的单次请求与返回流程：daily 不传日期，由上游返回最近内容；weekly/monthly 分别传当前 UTC ISO
周、自然月。周期与排序参数一起透传，成功响应按上游顺序交付，不因渠道日期缺失或跨度而拒绝、过滤论文，不额外探测日期或发第二次请求。窗口字段仅描述显式请求范围，daily
为上游默认而留空。

发现结果保留渠道列表实际返回的研究产物信号，不额外逐篇拉取详情，也不调用在线 S2。所有请求通过异步客户端执行，并发和超时有界。

### 3.4 输出、缺失与部分结果

- Paper 保存基础论文事实，metadata 按来源保留口径；不合并不同来源的引用数。
- 最终出口按 provider 保留清单制作独立副本。递归省略 metadata 空值，保留 0/false，不修改内部查询和缓存事实。
- execution 描述实际来源执行情况、缓存命中和错误。成功返回的已确认空事实与未执行、失败分开处理，不额外返回富化完成状态。
- 完整出口字段及压缩结构见 [README](../README.md#模型可见-metadata)。

## 4. S2 字段选择与请求约定

### 4.1 在线请求与出口字段

身份字段为 paperId、corpusId、externalIds、url、title；基础字段为 abstract、authors、year、publicationDate、venue。每次 lookup 获取相同字段集。

富化字段请求 tldr、citationCount、influentialCitationCount、openAccessPdf。停止请求 citationStyles，BibTeX 不再作为有效信号。

出口保留 corpus_id、citation_count、influential_citation_count、venue、tldr 文本和独立阅读入口。paper_id、observed_at、TLDR model 及离线 release/文件路径/行号保留在内部事实中，不返回模型。

不请求作者画像、embedding、referenceCount、fieldsOfStudy、s2FieldsOfStudy 或额外出版实体。authors 只填充 Paper 姓名，不逐作者补查。

### 4.2 已验证协议与必要边界

- 单篇采用 `/graph/v1/paper/{id}`，批量采用 `POST /graph/v1/paper/batch`；`fields` 位于查询参数，`ids` 位于 JSON 请求体。
- S2 专用客户端最终发出的请求不含 `User-Agent`，包括移除 HTTP 客户端默认添加的值。认证使用 `x-api-key`；POST 使用 JSON
  Content-Type。此为本次实验确认采用的接入约定，不把所有 429 归因于 User-Agent。
- 不复制实验脚本的固定代理、硬编码密钥或关闭 TLS 校验设置；代理通过部署环境配置。
- 批量请求遵守供应商最多 500 IDs、响应不超过 10 MB 的限制；ETL 默认每批 100 项。用户 lookup 仍最多 20 项。
- `openAccessPdf.url` 的空字符串在接入处转换为无可用链接；`publicationDate` 可以为空，保留独立 year，不补造月份或日期。
- 不请求或使用 `isOpenAccess`；有明确 PDF URL 即作为阅读入口。
- 真实边界解析后信任内部数据，不重复逐字段校验，不为未知内部破坏状态增加回退。供应商参数错误应明确暴露，不静默裁剪字段或吞错。

### 4.3 OpenAlex 精选富化投影

保留 id、cited_by_count、venue、is_retracted；primary_topic 和 study_designs 只输出名称。MeSH 保留 descriptor_name、qualifier_name、is_major_topic 并去重。阅读信息只保留独立 landing_page_url/pdf_url，删除 OA 状态、文档版本、许可、分类编码、层级、记录更新时间和 ids 字典。

search 与 lookup 使用相同最终投影；来源缓存保留内部完整事实。字段结构见 [README](../README.md#模型可见-metadata)。

## 5. 本地论文库、初始化与发布

### 5.1 数据基线与事实归属

当前已有输入：

| 数据      | 位置及已知情况                                                                                |
|-----------|-----------------------------------------------------------------------------------------------|
| arXiv     | `third_party_data/arxiv-metadata-oai-snapshot.json`，约 5.26 GiB，JSONL                       |
| S2 papers | 仓库根目录 `s2_download/papers/`，60 个 gzip 分片，压缩约 49.48 GiB                           |
| S2 tldrs  | 仓库根目录 `s2_download/tldrs/`，30 个 gzip 分片，压缩约 6.13 GiB                             |
| S2 版本   | 下载脚本声明 `2026-10-20`；首次导入清单记录其声明来源及输入文件，不将文件名当作独立版本证明   |
| API 实验  | 仓库根目录 `s2_full_fields.json`、`s2_batch_results.json`，分别保存单篇和三项混合 ID 批量响应 |

Kaggle 页面显示约两日前更新，用户预计每周同步。首次 OAI 补采从 `2026-10-18` 开始，这是已接受的运行假设，不构成全库完整性的证明；以后重建可显式指定初始补采日期。

arXiv 源记录拥有基础论文事实；S2 数据集及 API 响应拥有各自提供的学术信号。DuckDB 保存处理后的来源事实、关联和任务进度，ES
是可重建的在线投影，Redis 是可丢弃的缓存与共享协调设施。HTTP 请求不写 DuckDB；在线 lookup 的 S2 结果写缓存，持久来源写入由单写者
ETL 负责。

### 5.2 首次初始化

1. 流式读取 arXiv，规范化 URL/`arXiv:` 前缀、版本后缀及新旧 ID，规范化 DOI，保留原始 ID、版本记录和基础元数据。
2. 首次提交日期取首次版本的 created；`update_date` 保持来源更新日期含义，不能与提交或版本发布日期混用。
3. 直接扫描 S2 压缩分片，仅保留具有 arXiv ID，或 DOI 能关联当前本地论文的必要记录及字段。先裁剪、关联再保存，不全量解压、不镜像整个
   S2 库。
4. 精确 arXiv ID 匹配优先；无直接匹配时才接受唯一、无冲突的 DOI。多个候选无法唯一判定时保留诊断，不向最终文档写任意候选的指标。
5. 按 Corpus ID 关联 TLDR，保留 `model`，当前输入显式选用 `tldr@v2.0.0`。不同 model 的记录不任意覆盖，选择其他版本需明确修改输入配置。
6. 生成统一 arXiv 文档并发布 ES；未匹配 S2 或字段未提供时保留缺失，不能以 0、false 或展示占位文本代替。

实际离线映射：

| 文件字段                          | 用途                           |
|-----------------------------------|--------------------------------|
| `papers.corpusid`                 | S2 跨数据集关联键              |
| `papers.externalids.ArXiv/DOI`    | 精确关联本地论文               |
| `papers.citationcount`            | 引用量                         |
| `papers.influentialcitationcount` | 影响力引用量                   |
| `papers.isopenaccess`             | 开放获取布尔值                 |
| `tldrs.corpusid/model/text`       | 指定模型的 TLDR，text 可以为空 |

离线文件不假定有独立 S2 Paper ID、PDF URL 或 BibTeX。在线 API 能取得的字段不能反向成为离线文件必填项。缺少 Paper ID 时可通过
`CorpusId:` 或已有 arXiv/DOI 请求 API，不从 URL 猜测 Paper ID。

### 5.3 ES 投影与发布恢复

- 一篇 arXiv 论文一个文档，使用规范化 arXiv ID；在线无需跨索引连接来源表。
- 显式声明 mapping：标识符、分类为 keyword，标题、摘要、TLDR 为全文字段，日期为 date，引用量为整数，开放状态为可空布尔值。
- 文档保存来源 release 或 API 观测时间；不生成 `has_s2_match`、质量标签或入库综合分数。
- 首次发布使用版本化索引，Bulk 逐项确认后检查文档数、唯一 ID 及抽样内容，再切换服务别名。
- DuckDB 变更与待发布集合在本地事务中提交；ES 成功项确认后移出待发布集合。失败保留可重放进度，完成后才推进已发布标记。
- 增量允许逐批可见，保证单篇文档自洽；不建设 DuckDB 与 ES 的分布式事务。

## 6. 每日同步与恢复

### 6.1 每日 arXiv + S2 API

1. 使用官方 OAI-PMH `https://oaipmh.arxiv.org/oai`，按记录最后修改的 datestamp 同步，不能用论文首次提交日期作为更新游标。
2. 首轮采用上述补采起点，后续 from 取上次成功处理的服务端响应日期，不设置 until；分页和重放幂等。分页 token
   过期时从已确认范围重新获取，不跳过未完成记录。
3. 导入新增及修改的基础记录，基础论文可先发布，不等待 S2 收录。
4. 对新增、修改与上一轮待补充论文按精确 ID 批量调用 S2 Graph API，显式请求 `embedding.specter_v2`。同步引用、TLDR 和 768 维
   v2 向量，不启动 GPU。
5. API 结果与向量在 DuckDB 事务中保存，失效 S2 缓存后发布 ES；发布成功后失效 arXiv 缓存及别名，全部成功才推进 OAI
   游标。持久失效待办保证发布后缓存失效失败也能重放。S2 暂未收录或缺向量时保留待办。

### 6.2 向量及断点维护

存量论文使用本次 GPU FP16 proximity 灌库；日后向量由 S2 API 提供。标题或摘要改变会使旧向量失效；仅引用/TLDR
变化复用有效向量。发布附带匹配当前输入的向量，删除论文同时清理向量与待办。

不再使用 S2 release diff，不下载大包，不每日回扫原始分片。网络、429
和发布失败使任务非零退出，保留已提交数据、待办及未推进游标；重跑原命令续接。操作见 [维护文档](../deploy/README.md)。

## 7. 运行配置、凭证、限流和缓存

### 7.1 现有配置通道

- 服务名 `wisepen-scholar-service`，Python 包 `scholar`，默认端口 `19916`，可配置覆盖。
- Bootstrap 沿用 Common 的 Nacos、日志和服务身份方式；业务字段在 `core/config/app_settings` 明确声明，复用现有服务的加载机制。
- 复用现有 ES、Redis、Nacos；ES 使用 `ELASTICSEARCH_URIS`、`ELASTICSEARCH_USERNAME`、`ELASTICSEARCH_PASSWORD`，Redis 使用
  `REDIS_URL`，平台 S2 凭证使用 `S2_API_KEY`。本地 env 与 Nacos 使用同名字段。
- OpenAlex 是每用户、每工具的可选配置，search 与 lookup 分别保存，秘密字段沿用 `api_key` 通道。MCP 从 `_meta` 读取并以内部
  `X-OpenAlex-Api-Key` 请求头传给 Scholar，不暴露给模型或写入日志、响应、缓存。
- Scholar 不以服务端 `OPENALEX_API_KEY` 回退用户凭证；本地 BYOK 验收同样通过请求配置注入。
- Chat 的 `required_keys=()` 必须允许未配置用户继续使用本地工具；保留 Web Search 等必需配置工具的现有行为，工具选择和禁用仍使用已有机制。
- 内部 HTTP 沿用仓库的可信来源、安全上下文、Nacos RPC 和响应包装；不新建用户密钥管理或认证协议。

### 7.2 S2 跨进程合流、双通道与生命周期

- 在线平台请求先查缓存，再进入 Redis 统一合流。原子入队去重 ID、保存调用位置，每个首发 batch 最多 300 个不同 ID，支持配置 1～500。
- NOKEY 首发无 key，仅 HTTP 429 将原 batch 投递平台 KEY；其他 HTTP 错误和网络异常直接失败。两路默认各间隔 1.5 秒，独立配置、独立发射锚点。
- 收集间隔为相邻实际发射的最小间隔；首轮收集一轮，满批不提前发车，空批不发车，延迟不追赶，HTTP 响应等待不阻塞下一次发射。
- 在线新建首发批次应用 `SCHOLAR_S2_MAX_QUEUED_AHEAD`（默认 3），前方 3 批允许、4 批拒绝；合入已有批次不另占队列项。KEY 兜底不做队长准入。
- 每个实例使用一个 Redis 回执流，将跨进程结果按原调用顺序、重复位置及 null 分发。`execution.queued_ahead` 只保留首发快照，缓存命中不携带旧排队信息。
- BYOK 直接进入按 key 指纹隔离的 KEY FIFO。离线不参加线上合流，使用独立的 NOKEY → 429 → KEY 双通道，复用平台 key；KEY 与在线共用发射锚点，在线兜底优先。
- 两路消费者以 Redis 租约保证每路唯一有效发射者；实际启动 HTTP 后记账、释放队首。取消只解除该调用关联；整批无人等待时不发送。已领取但因崩溃结果不明的批次不自动重放。
- 协调器随服务 lifespan 启停，任务和回执设 TTL；Redis 故障不得绕过队列。发布时在线与离线一并更新，避免混跑旧许可协议。线上 S2 调用预算默认 25 秒，离线使用 ETL 请求预算。

### 7.3 缓存

| 对象                             | 默认 TTL | 隔离与语义                                                               |
|----------------------------------|----------|--------------------------------------------------------------------------|
| 查询候选及来源排序               | 10 分钟  | 各来源跨用户及凭证共享，包含查询条件与来源版本                            |
| HF 发现列表                      | 15 分钟  | 跨用户及凭证共享，包含领域、实际周期及排序                                 |
| 完整论文 lookup                  | 24 小时  | arXiv/S2/OpenAlex/HF 分来源按稳定身份和别名共享，不含凭证、领域、purpose |
| 已确认未找到或字段为空的查询结果 | 1 小时   | 区分成功确认与网络失败，避免重复请求                                     |

TTL 均可配置。search、discover、lookup 均先查共享缓存，命中不要求上游凭证，未命中才使用 BYOK 或平台凭证访问来源；用户标识和凭证不参与缓存键、Redis 命名空间或 lookup 在途复用键。缓存事实与本次执行状态分别构造，不缓存上次的执行状态。旧凭证隔离键不再读取，自然过期，不做兼容读取或迁移。

原始输入、DuckDB、发布产物及日志排除出 Git 和 Docker 构建上下文；不删除或搬迁用户数据。实验脚本中的凭证改为配置读取，正式实现不携带硬编码密钥。

## 8. 可分期验收与提交的子计划

每一期随实现提交相关行为测试、运行说明及本节完成状态。各期只引入当前实际调用的依赖和模型，完整 V1 以 P6
完成后的全部契约验收；不执行生产部署或夹带其他服务的无关修改。

### P0：计划、数据基线与提交准备

- **交付**：本计划、实际输入字段映射、数据与版本清单；原始大文件及运行产物的忽略规则；实验脚本凭证读取整改。
- **验收**：明确初始化输入和声明版本；样本可解析；单篇、批量 JSON 证据可追溯；Git 与镜像上下文不包含数据集或凭证。样本通过不表示
  gzip 全部完整或全库数据已导入。
- **边界**：不新增来源，不迁移输入数据，不把实验脚本的网络设置当成服务默认配置。
- **提交**：`docs(scholar): define v1 contracts and data baseline`。
- **状态**：实现与数据基线检查完成，已独立提交供审阅；全量数据验收分别记录于 P1/P2。

### P1：arXiv 本地检索闭环

- **交付**：补齐空 pyproject、接入 uv workspace；配置、客户端生命周期、就绪检查；独立 ETL CLI 的 arXiv 初始化与 ES 发布；本地
  search、lookup details 的 HTTP/MCP 调用闭环。
- **事实与投影**：DuckDB 基础论文及导入进度，版本化 ES 索引；基础查询缓存。
- **验收**：先以真实小样本贯通，再完成现有全量 arXiv 导入；真实 ES 检查数量、关键词、领域、时间、arXiv/DOI
  精确查询；中断恢复和重复运行不产生重复论文。未完成索引不切换服务别名。
- **边界**：不调用 OpenAlex、在线 S2 或 HF；只交付本地能力，不声称最终领域富化已完成。
- **提交**：`feat(scholar): deliver local arxiv search and lookup`。
- **状态**：已提交 `45231bef`；全量 3,224,542 篇已发布，真实 ES/HTTP/MCP 验收与重复运行验证通过。

### P2：S2 离线富化

- **交付**：直接扫描现有 papers/tldrs，完成选择性保留、精确关联、TLDR model 选择、来源追溯和 ES 富化发布。
- **验收**：全量流式读取验证 gzip 和 JSON 完整性；统计扫描、保留、匹配、歧义和缺失；验证 DOI-only 候选、0/false、空 TLDR、指定
  model、无 S2 关联及重复执行。ES 每篇仍只有一个基础文档，抽样与 DuckDB 一致。
- **边界**：不请求在线 API 补齐整个历史库，不将在线独有字段变为离线必填项。
- **提交**：`feat(scholar): enrich local papers from s2 datasets`。
- **状态**：离线导入、关联及发布实现完成，14 项受影响 ETL 测试通过；真实 ES 小样本投影及重复运行通过。90
  个分片已全部读取；内存不足修复后正在合并已筛选的 S2 信息，再更新检索索引，完整发布仍待验收。

### P3：OpenAlex BYOK 与显式多源检索

- **交付**：OpenAlex 按工具请求接收可选用户凭证；`FieldPolicy` 声明有序 searchers、领域 field_enrichers 和 discoverer；多源融合与
  OpenAlex 精确 details lookup 接入现有用例。
- **验收**：凭证不进入模型参数、请求 URL、缓存明文或日志；默认单源、缺凭证计划、未映射路由符合策略；只在多源已有成功来源时保留其他来源错误文本，单源和全来源失败传播
  ProviderError；精确 lookup 保留顺序、重复项、未找到、歧义及真正部分成功。
- **扩展边界**：字段映射仍集中在 `providers/field_mapping.py`；来源名是开放字符串，provider 可实现多个能力。医疗 PubMed/WoS
  mock 用例通过 provider 注册和 FieldPolicy 配置增加检索源及临床试验后处理，不修改核心编排算法；这不代表已接入真实
  PubMed/WoS 服务。
- **边界**：不建设共享凭证模型，不加入 S2 关键词召回或暗中叠加其他来源；可选搜索后处理失败保留基础候选和原错误文本。
- **提交**：`feat(scholar): add openalex byok and multisource search`。
- **状态**：已完成；71 项 Scholar 定向测试、8 项 MCP 工具测试及真实 ES/Redis/Nacos/OpenAlex/HF
  链路通过，见 [P3 验收记录](docs/p3-acceptance.md)。

### P4：purpose 与 field 驱动的富化编排

- **交付**：lookup 三种 purpose、固定的精选字段集、单篇/批量适配、论文缓存、全局限流和有界等待。
- **验收**：真实批量请求不含 User-Agent、不使用 authors 子字段；混合 ID 正确映射，PDF 与 BibTeX 可用；discover 不逐篇
  lookup；三种 lookup purpose 共用完整获取；成功通用 searcher 的搜索不再触发通用富化。三种 purpose 按有效信号短路，0/false
  保留真实含义。验证跨进程合流、独立发射间隔、同请求复用、取消及超时/429
  部分结果。
- **边界**：不获取 embedding、作者画像等弃选字段，不预置逐作者补查、未知字段自动裁剪或持久用户任务队列。
- **提交**：`feat(scholar): add purpose-aware s2 enrichment`。
- **状态**：已实现并接入 lookup；公开行为、HTTP、真实 Redis 配额、保存响应投影及正式 S2 Graph
  成功请求已验收，见 [P4 验收记录](docs/p4-acceptance.md)。

### P5：CS 专用 HF 富化与论文发现

- **交付**：完成异步 HF HTTP、Nacos `HF_TOKEN` 与 CS 搜索后处理的验收，并接入无 query 的 discover、周期结果和相关缓存。
- **验收**：同样的 CS 候选在 General 请求下不执行 HF、明确 CS 请求下执行；缓存满足时不重复调用；无 arXiv ID 不猜测匹配；HF
  无记录和服务故障不丢弃基础论文。验证日/自然周/月、recent/trending 官方请求参数、响应保序交付、异步调用、并发预算与三个工具间的边界；不自行按日期拒绝或过滤成功响应。
- **边界**：不加入主题发现搜索、全时热门榜、学术质量分数或候选内容推断领域。
- **提交**：`feat(scholar): add cs enrichment and paper discovery`。
- **状态**：发现按官方 SDK 的单次请求与响应转换流程收敛；daily
  使用上游最新内容默认值，周/月及排序参数透传，成功响应不做日期拒绝或过滤。见 [P5 验收记录](docs/p5-acceptance.md)。

### P6：持续同步、恢复与运行交付

- **交付**：每日 OAI + 批量 S2 API（含 specter_v2）、发布恢复、外部调度说明；补齐 Scholar 镜像、Compose 和 Jenkins 接入配置。
- **验收**：真实验证新增、普通修改、身份变化、S2 暂未收录、旧 DOI 记录通过 API 关联、OAI 删除和向量失效；单独缺附加字段不触发
  API，在线 KEY 兜底与 ETL 共享凭证发射节奏，在线优先。注入 Bulk 部分成功后失败，重放完成且未提前推进游标；基础论文不被 S2
  删除误删；构建上下文排除数据集和凭证，部署部分仅验收配置及说明。
- **边界**：不在 HTTP 进程运行 ETL，不因同步引入跨库事务。部署部分交付配置和运行说明，不启动本机
  Docker、不构建镜像、不执行生产部署；这些操作不是本次完成门槛。
- **提交**：`feat(scholar): add incremental synchronization and deployment`。
- **状态**：实现已完成，包含独立同步 CLI、OAI 3 秒串行请求、API 待办重放、向量持久化和部署模板；阶段全套 165 项测试及 Python
  包构建通过。真实同步需等待 P2 完整发布基线，当前验收范围见 [P6 记录](docs/p6-acceptance.md)，未将其标为真实运行验收完成；本机
  Docker 和生产部署不纳入验收。

## 9. 验证原则与证据

### 9.1 验证方式

测试围绕稳定用例、HTTP/MCP 协议和真实存储的可观察结果，不锁定私有 helper、目录形状、内部批大小或固定日志文案。框架已经解析的内部请求和自有
writer 生成的数据不逐层重复验证。

每期先完成受影响测试、静态检查及 `git diff --check`，再做一次对应真实边界验收。外部依赖不可用时，明确记录未验收，不能用替身测试替代真实
ES、Redis、Nacos 或供应商协议的成功结论。没有新变化、失败或未解问题时不重复扫描或测试。

### 9.2 当前证据与尚未验证事项

- 已完成 arXiv 全量导入：3,224,635 条输入形成 3,224,542 个规范论文 ID；81 组重复 ID 按来源更新日期确认保留最新记录。S2
  目前仅完成分片样本检查，完整性检查属于 P2。
- `../../s2_full_fields.json` 是单篇对象；`../../s2_batch_results.json` 是三项数组，含两篇 arXiv ID 和一项 Corpus ID
  的结果，已确认父字段 authors 返回 authorId/name，以及 PDF、BibTeX、TLDR 等所选字段的形态。
- 样本实际存在空 PDF URL、空 publicationDate，以及开放获取标记和 PDF 状态不一致的情况；按来源合法缺省处理，不把它们当成整篇失败。
- 不再以先前带 User-Agent 或 authors 子字段的失败请求判定精选字段批量不可用，也不重复执行已保存的实验。
- HF provider 使用原生异步 HTTP，按官方 SDK 的请求与返回流程实现；成功响应不自行按日期拒绝或过滤。日/周/月 trending 的真实
  HTTP、缓存及 MCP 调用通过，见 P5 验收记录。
- P3 的多源路由、OpenAlex lookup、错误传播及医疗多源扩展行为测试见 `tests/test_multisource_search.py`、
  `tests/test_openalex_lookup.py`、`tests/test_candidate_enrichment.py` 和 `tests/test_openalex_provider.py`
  ；最终结果见 [P3 验收记录](docs/p3-acceptance.md)。
- P1 行为测试 14 项、MCP 全套测试 103 项已通过；真实 Nacos、ES 8.19.18、Redis、全量别名发布及 HTTP/MCP 端到端验收通过，重复导入/发布维持
  3,224,542 篇、pending=0。部署产物属于 P6。详见阶段验收记录。

官方协议参考：[S2 Graph API](https://api.semanticscholar.org/api-docs/)、[S2 Datasets API](https://api.semanticscholar.org/api-docs/datasets)、[HF HfApi](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api)、[arXiv OAI-PMH](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/oa/index.md)、[OpenAlex 过滤](https://help.openalex.org/api/filtering/)。
