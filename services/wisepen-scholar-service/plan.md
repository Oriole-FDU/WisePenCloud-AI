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
| OpenAlex                  | 通过用户 BYOK 提供跨学科学术检索及精确查询                                        |
| S2                        | 提供通用学术信号；离线数据集、在线精确查询和每日同步各有明确调用边界              |
| Hugging Face Papers（HF） | 提供 Computer Science 专用富化和趋势发现，使用异步 HTTP API                       |

V1 不建设全文语料库、向量检索、完整引用图、作者研究工具、用户密钥管理系统或研究问题拆解流程。不预生成经典、热门、高影响力等学术判断标签，也不把不同来源的指标相加。

### 1.1 不得混淆的富化入口

| 入口                                          | 通用 S2 数据           | 在线 S2                              | 在线 HF                          |
|-----------------------------------------------|------------------------|--------------------------------------|----------------------------------|
| `search_papers(field=general)` 及其他普通领域 | 复用已有离线数据、缓存 | 禁止                                 | 不执行专用富化                   |
| `search_papers(field=computer_science)`       | 复用已有离线数据、缓存 | 禁止                                 | 执行 CS 配置的专用富化，优先缓存 |
| `lookup_papers(purpose=details)`              | 返回已有数据           | 禁止                                 | 不调用                           |
| `lookup_papers(purpose=enrich/full)`          | 优先复用已有数据       | 核心引用指标或 TLDR 缺失时允许       | 不调用                           |
| `discover_papers`                             | 可以复用已有通用数据   | 禁止                                 | 使用 HF 发现及必要详情能力       |
| 每日 arXiv 同步                               | 复用本地关联和信号     | 对新增、身份变化及待补充论文批量富化 | 不调用                           |

领域专用富化由请求选择的配置决定。General 即使查到了 CS 论文，也不推断其领域、执行 HF 富化或附加 CS 专用缓存。lookup
没有领域参数，不根据论文内容推断并触发 HF。S2 不提供 V1 在线关键词搜索路径。

### 1.2 工程架构约定

沿用 Chat/Sandbox 的典型微服务结构：`main.py` 管理日志、观测、生命周期与注册；`container.py` 装配依赖；`api` 提供薄 HTTP 接入；`application` 按用例编排；`core/storage` 实现存储读模型；`etl` 独立执行离线任务。

`domain/models/` 中的 `paper.py`、`identifiers.py` 只保存论文和身份领域语义。请求、结果、来源执行状态等细节放在具体应用用例或其共享执行模型中，不放入一个总 models.py。

`application/` 只有 `search/` 与 `lookup/` 两个用例包。`search/use_case.py` 负责来源计划、检索缓存和候选融合；`search/enrichment.py` 是搜索用例内的可选候选后处理，不是独立 application service。精确查询及其 S2 purpose 富化归 `lookup/` 内部；P4 的在线 S2 对外接入仍单独验收。

`application/field_policies.py` 是 search 与 lookup 共用的领域策略表。`FieldPolicy` 明确记录默认来源计划、无凭证计划、多源计划、搜索后处理器和发现器；容器注册来源名称到 provider 能力的绑定。新增医疗来源时，兼容的 PubMed/WoS searcher、富化器及容器注册可以由领域配置选择，无需在核心检索、缓存或融合流程增加供应商分支。`Paper.source` 接受开放字符串，来源专有事实放在 `metadata[来源名]` 自由字典中。

`providers/field_mapping.py` 仍是通用领域到 arXiv/OpenAlex 参数的统一映射文件。`providers/` 承载在线能力适配：本地 arXiv 搜索由 `ArxivProvider` 调用论文仓储，OpenAlex 实现同一 `PaperSearcher` 能力；HF 使用原生异步 HTTP，当前由 CS 搜索后处理调用。arXiv OAI 抓取和 S2 数据集、release、差分处理属于独立 `etl/` 步骤。

`domain/interfaces/` 按业务能力声明 `PaperSearcher.search()`、`PaperEnricher.enrich()`、`PaperDiscoverer.discover()`，不按供应商名称声明端口。一个 provider 可实现一个或多个能力。已知外部 I/O 由 provider 转为 `ProviderError`，保留原异常文本和 cause，不附带 reason/status 分类；程序错误和取消继续传播。S2 在线与离线调用共用 `core` 中的 HTTP 传输和 Redis 发送配额，不将离线方法放入 provider。

## 2. 领域映射与检索路由

### 2.1 V1 领域映射

下表是领域分类映射的唯一来源。OpenAlex 默认按 `topics.field.id` 过滤，多个 ID 使用 `|` 表示同字段 OR；法学使用明确列出的
subfield。arXiv 多分类按 OR 查询。

这些是不同分类体系之间的召回映射，不宣称一一精确等价。S2 不承担关键词召回，因此不维护未被消费的 S2 `fieldsOfStudy` 搜索参数。

| 请求值                             | 领域                                 | OpenAlex 过滤值           | arXiv 分类                                                                                                   | 有 BYOK 时默认来源与覆盖说明               |
|------------------------------------|--------------------------------------|---------------------------|--------------------------------------------------------------------------------------------------------------|--------------------------------------------|
| `general` | 跨学科、通识、未定领域 | 不过滤 | 全部分类，不过滤 | OpenAlex；无密钥时仅代表整个本地 arXiv 库 |
| `computer_science` | 计算机、AI、软件工程、数据科学 | `17` | `cs.*` | arXiv；按 CS 配置执行 HF 富化 |
| `medicine` | 临床、基础医学、药学、护理、口腔 | `27,29,30,35,36`（OR） | 无对应分类 | OpenAlex；无密钥时不支持该领域召回 |
| `biology` | 生物、遗传、神经科学、农学生命 | `11,13,24,28,34`（OR） | `q-bio.*` | OpenAlex；arXiv 仅覆盖定量生物学等相关方向 |
| `engineering` | 机械、电气电子、土木、化工、能源控制 | `15,21,22`（OR） | `eess.*` | OpenAlex；arXiv 主要覆盖电气工程与系统科学 |
| `economics_business` | 经济、金融、管理、工商会计 | `14,20`（OR） | `econ.*,q-fin.*` | OpenAlex；arXiv 覆盖经济与定量金融相关方向 |
| `mathematics` | 纯数、应用数学、统计、运筹 | `26,18`（OR） | `math.*,stat.*` | arXiv |
| `physics` | 物理、天文及相关方向 | `31` | `physics.*,astro-ph.*,cond-mat.*,gr-qc,hep-ex,hep-lat,hep-ph,hep-th,math-ph,nlin.*,nucl-ex,nucl-th,quant-ph` | arXiv |
| `chemistry` | 有机、无机、物理化学、分析化学 | `16` | `physics.chem-ph` | OpenAlex；arXiv 主要覆盖化学物理 |
| `psychology` | 认知、应用心理、脑与认知科学 | `32` | 无对应分类 | OpenAlex；不强制映射到其他 arXiv 分类 |
| `social_sciences` | 社会、政治、公共管理、传播、教育 | `33` | `physics.soc-ph` | OpenAlex；arXiv 主要覆盖社会物理与计算方向 |
| `arts_humanities` | 文学、历史、哲学、艺术、语言学 | `12` | 无对应分类 | OpenAlex |
| `law` | 法学理论、民商、国际法、刑法 | `topics.subfield.id:3308` | 无对应分类 | OpenAlex；不静默扩大到整个社会科学 |
| `materials_environmental_sciences` | 材料、地学、环境生态、气象地理 | `19,23,25`（OR） | `cond-mat.mtrl-sci,physics.ao-ph,physics.geo-ph` | OpenAlex；arXiv 仅覆盖对应交叉物理方向 |

### 2.2 默认与多源计划

`source_scope` 使用固定枚举 `default`、`multi_source`。

- `default`：有 OpenAlex 密钥时按上表选一个默认召回源，不自动叠加其他来源。无密钥时，General 查询本地全库；有 arXiv
  映射的领域按映射查询；无映射则报告能力不可用。
- `multi_source`：General 和有 arXiv 映射的领域注册 arXiv + OpenAlex 计划；无对应 arXiv 分类的领域暂不注册该计划。不得把单源执行描述为完整多源检索。
- 多源计划缺少所需凭证或 provider 绑定时，在执行来源调用前报能力不可用；不静默退回默认源。
- 单源 ProviderError 直接传播；多源只有至少一个来源成功才返回其结果，并在失败来源执行记录中保留原错误文本。所有来源都失败时传播 provider 错误；程序错误和取消不会被改写为来源部分失败。
- 来源故障不隐式改变默认来源或放宽领域过滤。

候选按规范标识符归并，不能只凭标题相似度认定同一论文。优先 arXiv ID，再使用无冲突的 DOI 等精确身份；标识冲突保留诊断，不任意合并。多源使用等权
RRF，复用 Common 已有算法，不直接比较不同来源的原始分数。单源保留原生排序；HF 和引用指标不重排相关性结果。

领域配置表达映射、默认来源、多源计划和专用富化器。通用执行器执行选中的配置，不检查论文内容再选择领域。V1 不建设动态插件加载或分阶段条件召回框架。

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

明确请求 CS 时，在候选归并并截取最终数量后执行 HF 富化。优先缓存，仅对有 arXiv ID 的候选请求 HF 论文详情，并发数默认
4，受本次请求预算约束。HF 使用 `httpx.AsyncClient`，可选平台凭证来自 Nacos `HF_TOKEN`，不使用同步 SDK 或 `to_thread`。保留点赞、代码仓库、关联模型和数据集的实际返回值；不抓取整个关联仓库目录，不假定返回条目穷尽所有产物。404
表示 HF 没有对应记录，其他已知外部 I/O 错误保留原文本并标记可选后处理未完成；均保留基础论文结果。程序错误和取消继续传播。

### 3.2 `lookup_papers`

| 参数          | 类型与默认值                                | 语义                                                                                |
|---------------|---------------------------------------------|-------------------------------------------------------------------------------------|
| `identifiers` | 必填字符串列表，1–20 项                     | DOI、arXiv ID、带明确命名空间的 OpenAlex ID、S2 Paper ID、Corpus ID，或相应官方 URL |
| `purpose`     | `details`、`enrich`、`full`，默认 `details` | 控制详情投影与在线富化权限                                                          |

| purpose   | 基础信息           | 富化                     | 在线 S2  |
|-----------|--------------------|--------------------------|----------|
| `details` | 完整返回可用详情   | 返回已有信息，不主动补齐 | 禁止     |
| `enrich`  | 只返回必要论文身份 | 获取约定的高价值信号     | 条件允许 |
| `full`    | 完整返回可用详情   | 同时执行 enrich 策略     | 条件允许 |

具体规则：

1. details 优先读取本地 arXiv 和缓存；未满足详情需求且该工具配置了 OpenAlex 密钥时，可以按精确 ID 查询
   OpenAlex。没有密钥时仍可返回本地及缓存结果，不静默调用 S2。
2. enrich/full 优先读取离线数据和缓存；只有 `citationCount`、`influentialCitationCount` 或 TLDR 缺失时，才允许在线 S2。单独缺
   PDF、BibTeX、开放状态或作者信息不触发联网。
3. 已有核心字段满足需求时直接返回；成功请求确认字段为空时，在缓存有效期内不重复请求。`0`、`false` 是已知值，不视为缺失。
4. enrich 只请求身份与富化字段；full 缺基础信息时才增加基础字段。已知基础信息不因富化需要重新获取。
5. 没有 OpenAlex 密钥时，enrich/full 仍可通过平台 S2 按精确 ID 查询；full 可以使用同次 S2 响应取得所需基础信息。
6. 批量结果按输入顺序逐项报告。重复输入可以合并 I/O，但保留对应关系；有可用项时，其他项的 provider 失败保留在对应结果中。整批没有可用结果且 provider 调用全部失败时传播首个原始 ProviderError。
7. 不新增 `include_signals`、`refresh`、`provider` 或 lookup 领域参数。

### 3.3 `discover_papers`

| 参数     | 类型与默认值                                 | 语义                                 |
|----------|----------------------------------------------|--------------------------------------|
| `field`  | 必填发现领域枚举，V1 只有 `computer_science` | 仅暴露已注册的发现能力               |
| `period` | `daily`、`weekly`、`monthly`，默认 `daily`   | 渠道日期或自然周期，不是滚动 7/30 天 |
| `sort`   | `recent`、`trending`，默认 `trending`        | `recent` 映射为上游 `publishedAt`    |
| `limit`  | 默认 10，范围 1–15                           | 最终返回数量                         |

删除 `query`、`all_time`、`popular`。发现以渠道分发信号为入口，不执行主题搜索。

使用 HF 官方论文 HTTP API：daily 获取最近可用内容；weekly/monthly 分别传当前 UTC 自然周、自然月，返回实际查询窗口。按需使用同一 HF 适配器的详情和缓存补充研究产物，不调用在线 S2。所有请求通过异步客户端执行，并发和超时有界。

### 3.4 输出、缺失与部分结果

- `domain/models/paper.py` 保留跨来源的核心身份和基础事实；供应商附加信息使用按来源名分组的自由 `metadata` 字典，约定优于配置。Paper 不依赖 ArxivMetadata 等供应商类型。字典仍遵守本文精选字段及事实来源约定，不全量接受上游字段。
- 保留事实来源、来源 release 或观测时间，以及真实使用的论文与全文链接。不同来源的数据分别保存，不相加或无依据覆盖。
- 报告实际执行来源、缓存命中、未执行或失败原因。来源执行情况与富化完成情况分别表达。
- 成功响应中的来源空值，与请求因限流、超时、服务故障而未完成分别表达；不能都作为已确认的空事实。`complete`
  表示约定获取已完成，不要求全部字段非空。
- 只为调用方实际消费的信息增加字段；不提前建设完整审计事件、通用状态机或任意字段组合框架。

## 4. S2 字段选择与请求约定

### 4.1 只保留六项富化数据

| API 字段                   | 工具保留内容     | 实际用途               |
|----------------------------|------------------|------------------------|
| `tldr`                     | 文本及来源 model | 快速理解论文贡献       |
| `citationCount`            | S2 引用量        | 查看引用规模           |
| `influentialCitationCount` | S2 影响力引用量  | 补充引用影响力信息     |
| `isOpenAccess`             | S2 提供的布尔值  | 保留来源的开放获取标记 |
| `openAccessPdf`            | 可用的 `url`     | 提供全文入口           |
| `citationStyles`           | `bibtex` 原文    | 引用写作               |

身份请求字段固定为 `paperId,corpusId,externalIds,url,title`。`externalIds` 只投影当前需要的 DOI 和 arXiv ID；Corpus ID
使用其独立字段。

富化请求使用身份字段加上六项富化字段；full 需要基础信息时再增加 `abstract,authors,year,publicationDate,venue`。API 请求选择父字段
`openAccessPdf`、`citationStyles`，在接入处投影实际需要的子字段。

以下数据不进入 V1 的请求、规范模型或索引：

- 作者机构、H-index、作者论文数及引用量：没有作者研究用例，不增加作者接口调用。
- `embedding`：没有向量检索消费者。
- `referenceCount`：当前工具不消费参考文献数量。
- `fieldsOfStudy`、`s2FieldsOfStudy`：不参与当前路由、过滤或排序。
- `publicationVenue`、`publicationTypes`、`journal`：使用 venue 展示发表渠道，使用 BibTeX 完成引用，不建设额外出版实体结构。

`authors` 仅解析 API 实际返回的姓名以填充基础 Paper，不保留没有消费者的作者画像。不请求 `authors.*`，不为其失败增加逐作者补查或字段自动拆分回退。机构等展示占位文案不作为来源事实保存。

### 4.2 已验证协议与必要边界

- 单篇采用 `/graph/v1/paper/{id}`，批量采用 `POST /graph/v1/paper/batch`；`fields` 位于查询参数，`ids` 位于 JSON 请求体。
- S2 专用客户端最终发出的请求不含 `User-Agent`，包括移除 HTTP 客户端默认添加的值。认证使用 `x-api-key`；POST 使用 JSON
  Content-Type。此为本次实验确认采用的接入约定，不把所有 429 归因于 User-Agent。
- 不复制实验脚本的固定代理、硬编码密钥或关闭 TLS 校验设置；代理通过部署环境配置。
- 批量请求遵守供应商最多 500 IDs、响应不超过 10 MB 的限制；ETL 默认每批 100 项。用户 lookup 仍最多 20 项。
- `openAccessPdf.url` 的空字符串在接入处转换为无可用链接；`publicationDate` 可以为空，保留独立 year，不补造月份或日期。
- `isOpenAccess` 与 PDF 子对象的状态可能不一致，分别忠实读取来源，不推导访问许可、不用其中一个改写另一个，也不新增一致性校验阻塞结果。
- 真实边界解析后信任内部数据，不重复逐字段校验，不为未知内部破坏状态增加回退。供应商参数错误应明确暴露，不静默裁剪字段或吞错。

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

## 6. 每日同步与 S2 release 更新

### 6.1 每日 arXiv + S2 API

1. 使用官方 OAI-PMH `https://oaipmh.arxiv.org/oai`，按记录最后修改的 datestamp 同步，不能用论文首次提交日期作为更新游标。
2. 首轮采用上述补采起点，后续 from 取上次成功处理的服务端响应日期，不设置 until；分页和重放幂等。分页 token
   过期时从已确认范围重新获取，不跳过未完成记录。
3. 导入新增及修改的基础记录，基础论文可先发布，不等待 S2 收录。
4. 对新增论文、身份标识变化或上一轮待补充记录，先复用本地关联和已有信号；只有 `citationCount`、`influentialCitationCount` 或
   TLDR 缺失时，才按精确 ID 批量调用 S2 API。单独缺 PDF、BibTeX 或开放状态不触发请求；普通标题、摘要等修改不重复获取已有信号。
5. API 结果按 Corpus ID 关联，持久记录 API 来源和观测时间，重新生成受影响 ES 文档。未收录或失败进入该 ETL
   任务的待处理进度，不永久解释为没有信号。

这个流程解决“初始化时未保留的旧 S2 DOI 记录，后来才对应到新 arXiv 论文”的关联缺口。V1 不为此建立全量 S2 标识符索引或每日回扫原始分片。

### 6.2 S2 release 差分

- 新 release 发布后，按官方版本链应用 `papers/tldrs` 的更新及删除，不跳过中间变更。
- 修改关联前记录旧关联影响范围，更新后同时考虑新关联，重新生成受影响论文。仍然只保留当前本地库相关数据。
- release 更新替换对应 S2 富化记录，删除移除对应数据及关联；失去有效关联时清除该来源的富化，不删除 arXiv 基础论文。
- API 补充记录标明其观测时间，release 记录标明数据集版本。release 替换后结果反映该 release 的事实，不混合不同记录或累计引用量。
- 每日任务和 release 任务串行写 DuckDB，不能并发覆盖彼此的暂存、关联或待发布集合。
- 受影响论文缓存失效，查询缓存按 TTL 过期。整轮变更与 ES 发布完成后才推进已完成 release。

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

### 7.2 S2 全局 1 RPS 与生命周期

- lookup、每日同步及数据集元信息请求统一经过 S2 Gateway，共享跨进程 Redis 协调的 1 RPS。下载签名对象存储分片与 S2 API
  请求分别处理。
- 用户调用有界同步等待，Scholar 总预算默认 25 秒，MCP 30 秒，预算覆盖排队及外部调用；取消或超时后不把本次请求转成持久后台任务。
- 只对本次仍缺失的论文批量请求，复用缓存及相同在途请求，避免逐论文、逐字段、逐作者 I/O。
- 429、网络超时或服务故障按外部边界处理；等待或重试不能突破预算和全局限流。协调设施不可用时不得绕开限流继续请求 S2。
- ETL 使用自己的持久任务清单、批次进度和重放；这是离线同步恢复，不是用户请求的异步富化队列。
- ETL 使用独立 CLI，由外部调度每日触发或检查新 release；HTTP 生命周期只管理实际在线客户端，不启动 ETL。

### 7.3 缓存

| 对象                             | 默认 TTL | 隔离与语义                                                    |
|----------------------------------|----------|---------------------------------------------------------------|
| 查询候选及来源排序               | 10 分钟  | 本地可共享；OpenAlex 按用户和凭证指纹隔离，包含查询条件与计划 |
| HF 发现列表                      | 15 分钟  | 包含实际周期及排序                                            |
| 公共论文与富化数据               | 24 小时  | 按规范身份共享，保留字段来源和观测时间                        |
| 已确认未找到或字段为空的查询结果 | 1 小时   | 区分成功确认与网络失败，避免重复请求                          |

TTL 均可配置。先检查来源可用性，再访问相应查询缓存；缺密钥不能借缓存伪装已完成 BYOK 多源计划。缓存事实与本次执行状态分别构造，不把上次的完成状态当作本次事实。

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
- **状态**：实现与数据基线检查完成，等待用户验收。

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
- **状态**：离线导入、关联及发布实现完成，14 项受影响 ETL 测试通过；真实 ES 小样本投影及重复运行通过。现有 90 个分片正在后台全量扫描，完整性和全量富化发布尚未验收。

### P3：OpenAlex BYOK 与显式多源检索

- **交付**：OpenAlex 按工具请求接收可选用户凭证；`FieldPolicy` 声明默认/无凭证/多源路由及搜索后处理；多源融合与 OpenAlex 精确 details lookup 接入现有用例。
- **验收**：凭证不进入模型参数、请求 URL、缓存明文或日志；默认单源、缺凭证计划、未映射路由符合策略；只在多源已有成功来源时保留其他来源错误文本，单源和全来源失败传播 ProviderError；精确 lookup 保留顺序、重复项、未找到、歧义及真正部分成功。
- **扩展边界**：字段映射仍集中在 `providers/field_mapping.py`；来源名是开放字符串，provider 可实现多个能力。医疗 PubMed/WoS mock 用例通过 provider 注册和 FieldPolicy 配置增加检索源及临床试验后处理，不修改核心编排算法；这不代表已接入真实 PubMed/WoS 服务。
- **边界**：不建设共享凭证模型，不加入 S2 关键词召回或暗中叠加其他来源；可选搜索后处理失败保留基础候选和原错误文本。
- **提交**：`feat(scholar): add openalex byok and multisource search`。
- **状态**：已完成；71 项 Scholar 定向测试、8 项 MCP 工具测试及真实 ES/Redis/Nacos/OpenAlex/HF 链路通过，见 [P3 验收记录](docs/p3-acceptance.md)。

### P4：purpose 驱动的 S2 在线富化

- **交付**：lookup 三种 purpose、固定的精选字段集、单篇/批量适配、论文缓存、全局限流和有界等待。
- **验收**：真实批量请求不含 User-Agent、不使用 authors 子字段；混合 ID 正确映射，PDF 与 BibTeX 可用；details/search/discover
  不产生在线 S2 请求；缺引用指标或 TLDR 才联网，0/false 和成功确认的空值不触发重复请求。验证跨进程 1 RPS、同请求复用、取消及超时/429
  部分结果。
- **边界**：不获取 embedding、作者画像等弃选字段，不预置逐作者补查、未知字段自动裁剪或持久用户任务队列。
- **提交**：`feat(scholar): add purpose-aware s2 enrichment`。
- **状态**：已实现并接入 lookup；公开行为、HTTP、真实 Redis 配额、保存响应投影及正式 S2 Graph 成功请求已验收，见 [P4 验收记录](docs/p4-acceptance.md)。

### P5：CS 专用 HF 富化与论文发现

- **交付**：完成异步 HF HTTP、Nacos `HF_TOKEN` 与 CS 搜索后处理的验收，并接入无 query 的 discover、周期结果和相关缓存。HF 已由 CS 搜索后处理使用；发现用例和完整 P5 验收仍未完成。
- **验收**：同样的 CS 候选在 General 请求下不执行 HF、明确 CS 请求下执行；缓存满足时不重复调用；无 arXiv ID 不猜测匹配；HF
  无记录和服务故障不丢弃基础论文。验证日/自然周/月、recent/trending、异步调用、并发预算与三个工具间的边界。
- **边界**：不加入主题发现搜索、全时热门榜、学术质量分数或候选内容推断领域。
- **提交**：`feat(scholar): add cs enrichment and paper discovery`。
- **状态**：待验收；依赖 P3 的领域检索计划，可独立于 P4 验收。

### P6：持续同步、恢复与运行交付

- **交付**：每日 OAI + 批量 S2 API、新 release 差分、发布恢复、外部调度说明；补齐 Scholar 镜像、Compose 和 Jenkins 接入配置。
- **验收**：真实验证新增、普通修改、身份变化、S2 暂未收录、旧 DOI 记录通过 API 关联、release 更新与删除；单独缺附加字段不触发
  API，在线 lookup 与 ETL 共享 1 RPS。注入 Bulk 部分成功后失败，重放完成且未提前推进游标或 release；基础论文不被 S2
  删除误删；完整镜像不含数据集和凭证。
- **边界**：不在 HTTP 进程运行 ETL，不因同步引入跨库事务，不做生产部署。
- **提交**：`feat(scholar): add incremental synchronization and deployment`。
- **状态**：待开发；依赖 P2/P4，最终 V1 验收同时要求 P3/P5 完成。

## 9. 验证原则与证据

### 9.1 验证方式

测试围绕稳定用例、HTTP/MCP 协议和真实存储的可观察结果，不锁定私有 helper、目录形状、内部批大小或固定日志文案。框架已经解析的内部请求和自有
writer 生成的数据不逐层重复验证。

每期先完成受影响测试、静态检查及 `git diff --check`，再做一次对应真实边界验收。外部依赖不可用时，明确记录未验收，不能用替身测试替代真实
ES、Redis、Nacos 或供应商协议的成功结论。没有新变化、失败或未解问题时不重复扫描或测试。

### 9.2 当前证据与尚未验证事项

- 已完成 arXiv 全量导入：3,224,635 条输入形成 3,224,542 个规范论文 ID；81 组重复 ID 按来源更新日期确认保留最新记录。S2 目前仅完成分片样本检查，完整性检查属于 P2。
- `../../s2_full_fields.json` 是单篇对象；`../../s2_batch_results.json` 是三项数组，含两篇 arXiv ID 和一项 Corpus ID
  的结果，已确认父字段 authors 返回 authorId/name，以及 PDF、BibTeX、TLDR 等所选字段的形态。
- 样本实际存在空 PDF URL、空 publicationDate，以及开放获取标记和 PDF 状态不一致的情况；按来源合法缺省处理，不把它们当成整篇失败。
- 不再以先前带 User-Agent 或 authors 子字段的失败请求判定精选字段批量不可用，也不重复执行已保存的实验。
- 已检查 HF 官方 SDK 对应的 HTTP 协议，当前 provider 使用原生异步 HTTP，且已由 CS 搜索候选后处理调用；公开 discovery 接线及 P5 最终验收仍待完成。
- P3 的多源路由、OpenAlex lookup、错误传播及医疗多源扩展行为测试见 `tests/test_multisource_search.py`、`tests/test_openalex_lookup.py`、`tests/test_candidate_enrichment.py` 和 `tests/test_openalex_provider.py`；最终结果见 [P3 验收记录](docs/p3-acceptance.md)。
- P1 行为测试 14 项、MCP 全套测试 103 项已通过；真实 Nacos、ES 8.19.18、Redis、全量别名发布及 HTTP/MCP 端到端验收通过，重复导入/发布维持 3,224,542 篇、pending=0。部署产物属于 P6。详见阶段验收记录。

官方协议参考：[S2 Graph API](https://api.semanticscholar.org/api-docs/)、[S2 Datasets API](https://api.semanticscholar.org/api-docs/datasets)、[HF HfApi](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api)、[arXiv OAI-PMH](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/oa/index.md)、[OpenAlex 过滤](https://help.openalex.org/api/filtering/)。
