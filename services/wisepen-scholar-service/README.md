# WisePen Scholar Service

Scholar 是内部 HTTP 微服务。MCP 工具由现有 `wisepen-mcp-service` 注册，Scholar 不启动独立 MCP server。

## 结构与迭代边界

- `api/schemas` 在 HTTP 边界校验完整契约；`api/endpoints` 只做 DTO 转换、预算控制及容器注入。
- application 提供 search、lookup、discover 三个用例（`search.py`、`lookup.py`、`discovery.py`）。`lookup_runner.py` 是检索后处理与 lookup 共用的内部执行机制；领域与策略模型统一定义在 `domain/models/fields.py`，通过 domain 的策略读取契约获取当前快照。
- `domain/models/paper.py`、`identifiers.py` 保存领域论文及身份；请求、结果、执行状态属于具体应用用例或共享执行模型。`Paper.source` 接受开放来源字符串，来源附加事实放在 `metadata[来源名]` 自由字典中，不依赖供应商专用领域类型。
- `domain/interfaces` 按搜索、富化、发现能力声明通用端口；provider 可实现一个或多个能力。`providers/field_mapping.py` 仍是领域分类到 arXiv/OpenAlex 来源参数的统一映射文件。
- `core/storage` 实现 ES 论文读模型和 Redis 查询缓存；`container.py` 装配依赖。
- `core/config` 与 Chat/Sandbox 一样，通过 Bootstrap 定位 Nacos，再由 AppSettings 加载业务字段；`main.py` 负责日志、观测、安全中间件、客户端生命周期和服务注册。
- `etl` 独立维护 DuckDB 来源事实及发布进度；HTTP 不读写 DuckDB，不启动 ETL。

P1 本地 arXiv、P3 配置驱动检索、P4 lookup 在线富化已验收；P2 正在合并本地 S2 信息并更新检索索引。P5 发现按 HF 官方 SDK 的请求与返回流程实现。CS 搜索与发现通过原生异步 HTTP 调用 HF。

P6 已提供独立 OAI/S2 差分同步与恢复、Compose/Jenkins/systemd 模板；真实完整同步的剩余验收见 [P6 记录](docs/p6-acceptance.md)。部署交付仅包含配置和说明，本次不启动 Docker、不构建镜像、不做生产部署。

单源检索按领域配置顺序回退，正常空列表也结束；多源执行已启用来源并报告结果。HF/OpenAlex 搜索和 HF 发现必须携带用户 BYOK，平台 key 只用于富化。缺 key 的搜索来源在读取缓存前跳过并记为 not_executed；没有可用来源返回 503 并说明所需 BYOK。

模板中的通用富化顺序为 arxiv→s2→openalex，可在 Nacos 调整，每篇有有效信号即停止；领域增量全部执行，初始仅 CS 注册 HF。搜索成功来源包含任一通用来源时跳过通用富化，领域富化排除本次成功的同源 searcher。供应商规则在各 provider 实现，由 `providers.policies.PROVIDER_POLICIES` 统一注册，新增临床增量只需 provider、注册和领域配置。平台不默认融合同名字段或做语义去重。

## 配置与启动

在本目录复制 `.env.example` 为 `.env`，填写现有 Nacos 引导凭证。业务配置使用 DEFAULT_GROUP 下的 `wisepen-scholar-service-dev.yaml`，字段见 [配置模板](docs/platform-settings.yaml)。prod 对应 `wisepen-scholar-service-prod.yaml`。不把凭证提交进仓库。

`SCHOLAR_POLICIES` 完整配置通用富化来源和全部领域的 `searchers`、`field_enrichers`、`discoverer`，列表顺序就是优先级。将 CS 的 `searchers` 改为 `[huggingface, arxiv]` 并发布 Nacos 后，收到更新的实例会优先使用 HF，无需重启。一次请求固定使用同一版策略。无效更新会记录错误并保留当前有效策略；其他业务设置仍在重启后生效。

Scholar HTTP 的领域参数接受全部 `AcademicField` 枚举；未配置 `discoverer` 的领域由 discover 用例返回能力不可用。MCP 服务独立决定工具参数的暴露范围。

在本目录执行：

```powershell
uv sync --inexact --package wisepen-scholar-service --extra test
uv run --no-sync uvicorn scholar.main:app --host 127.0.0.1 --port 19916
```

`GET /scholar/health` 检查真实 ES、Redis 及已发布别名，未就绪返回 503。与业务 API 一样，需要现有内部 `X-From-Source` 请求头。

## 初始化和恢复

```powershell
uv run --no-sync python deploy/run.py import-arxiv
uv run --no-sync python deploy/run.py status
uv run --no-sync python deploy/run.py publish
```

默认输入 `third_party_data/arxiv-metadata-oai-snapshot.json`，数据库 `.runtime/scholar.duckdb`。输入中的重复 arXiv ID 按最新 `update_date` 保留，同日取文件中后出现的记录。`--source`、`--db`、`--batch-size` 支持显式覆盖。`publish --alias` 可指定独立验收别名；正式别名默认 `wisepen-scholar-arxiv`。

导入按批次同时提交原始记录、规范投影、待发布 ID 和字节游标；直接重跑从 checkpoint 继续。相同输入重复运行不增加论文；源文件路径、大小或修改时间变化时拒绝复用原数据库，重建应指定新的 `--db`。DuckDB 文件锁保证单写者，不同时运行两个 ETL 写进程。

`--max-batches N` 限制本次工作量，适合验证恢复；未读到 EOF 的导入不能发布。发布逐项确认 Bulk，成功项移出待发布集合，失败项重放同一文档 ID。全部文档数量及内容抽样检查通过后，才原子切换版本索引别名。旧索引不自动删除。在线请求总预算默认 25 秒；独立 ETL 的单次存储请求超时默认 120 秒，通过 `SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS` 配置。资源有限的 ES 实例可以用 `publish --batch-size 1000` 减少单次写入压力。

离线任务可用[本地监控看板](docs/offline-dashboard.md)查看阶段进度、速率、真实退出码和错误日志。看板仅读取快照，不轮询数据库；现有 ETL 命令经 `deploy/offline_dashboard.py run` 包装后自动接入。

## HTTP 与 MCP

`POST /scholar/papers/discover` 与现有 MCP `discover_papers` 提供 CS 的 daily/weekly/monthly 渠道发现，默认 trending，支持 recent 和 limit，不接受 query。每次只发一个官方列表请求，直接交付成功响应；daily 使用官方最新内容默认值，weekly/monthly 传周期参数。不按渠道日期丢弃或过滤结果，见 [P5 验收记录](docs/p5-acceptance.md)。

Lookup 统一取得基础论文及可用调研 metadata，不再接受 purpose。响应为 `items[{identifier,status,paper,error}]` 与 `execution`；metadata 仅位于 `paper.metadata`。没有可用结果的外部失败直接抛出，已有论文的部分结果保留真实错误。历史验收见 [P4 记录](docs/p4-acceptance.md)。

`POST /scholar/papers/search` 接受 query、field、published_after/before、limit、source_scope。时间按首次提交日期包含两端过滤；领域采用 plan.md 的 arXiv 分类映射。查询缓存默认 10 分钟，缓存只存论文事实，每次重新构造执行情况。

本地检索由 ES 承担关键词召回，Qdrant 承担 SPECTER2 稠密召回；两路使用相同的学科和含边界的日期过滤，经 RRF 融合后加入有界引用先验。模型与参数见[混合检索](docs/hybrid-search.md)。DuckDB 已有向量可直接批量迁移，ES 不再保存 embedding。

`POST /scholar/papers/lookup` 接受 identifiers、field（默认 general），逐项保留输入顺序、重复输入、未找到、标识错误或 DOI 歧义。arXiv 版本和官方 URL 归一化，DOI 不区分大小写。没有本地来源映射的领域不会退回全库检索。所有业务响应使用 Common `R<T>`。

现有 MCP 服务的 search、lookup、discover 工具使用 Nacos RpcClient 调用这些 HTTP 接口，返回解包后的 data；来源路由和论文归一化不复制到 MCP。配置驱动的检索及医疗多源扩展验收见 [P3 记录](docs/p3-acceptance.md)。

S2 平台请求通过 Redis 跨实例合流：收集周期内按 ID 去重，每批最多 `SCHOLAR_S2_BATCH_MAX_IDS`（默认 300，范围 1～500）。NOKEY 首发不携带 key，仅 HTTP 429 将原批次投递平台 KEY 兜底；网络错误及其他状态直接失败。两路按各自上次实际发射时刻节流，间隔分别由 `SCHOLAR_S2_NOKEY_INTERVAL_SECONDS`、`SCHOLAR_S2_KEY_INTERVAL_SECONDS` 配置，默认均为 1.5 秒；满批不提前发车，慢响应不阻塞下一批发射。首轮收集一个间隔，之后空闲超过间隔时立即发出当前待办。

在线仅在新建首发批次时应用 `SCHOLAR_S2_MAX_QUEUED_AHEAD`（默认 3）：前方 3 批允许、4 批拒绝，合入已有批次不额外占队列项。KEY 兜底不做队长拒绝，由请求预算终止等待。Redis 回执按调用的原顺序还原重复 ID 与 null；`execution.queued_ahead` 是首发准入快照，缓存命中不携带旧排队信息。

用户 BYOK 直接使用按 SHA-256 指纹隔离的 KEY FIFO，不参加平台合流，不设队长阈值。提供与平台相同的 key 仍归平台对象。离线使用独立的 NOKEY → 429 → KEY 双通道及合流待办，不参加线上合流；复用平台 key，KEY 与线上共享发射锚点，在线兜底待办优先。离线 CLI 统一管理消费者启停，不再额外 sleep。消费者随服务 lifespan 启停，部署时线上与离线同时更新，避免混跑旧发送协议。

合流与分发的自动化验收使用独立 Redis 测试命名空间及 HTTP 替身，覆盖不同实例和独立进程，不消耗真实 S2 配额。

## 模型可见 metadata

search、lookup、discover 在最终出口统一调用 `application.paper_projection.project_paper`。各 provider 的 `project_metadata` 定义保留清单；内部信号、别名、完整性及 TTL 判断使用完整事实。search/discover 的 `candidates-v2` 缓存也保存投影前候选，lookup 缓存版本保持不变。

| 来源 | 保留内容 |
|---|---|
| arXiv | categories、updated_date、journal_ref、comments；versions 压缩为 latest_version；submitted_date 仅在不同于顶层 publication_date 时保留 |
| S2 | corpus_id、citation_count、influential_citation_count、venue、tldr 文本字符串及独立的 open_access_pdf 入口 |
| OpenAlex | id、cited_by_count、venue、is_retracted、primary_topic 名称、study_designs 名称列表、精简 MeSH、独立阅读入口、额外 arXiv 标识 |
| Hugging Face | ai_keywords、upvotes、github_repo、github_stars、submitted_at、linked_models、linked_datasets；没有 abstract 时保留 ai_summary |

- S2 TLDR 可结合摘要、引言和结论，保留文本；HF AI 摘要是 abstract 的一句话总结，已有 abstract 时省略。
- OpenAlex MeSH 仅含 descriptor_name、非空 qualifier_name、is_major_topic，按完整条目去重并保留不同限定词。研究设计沿用上游自动分类含义。
- 不展示或使用 OA 状态；OpenAlex best_oa_location 只保留独立 landing_page_url、pdf_url。同一 URL 在该来源内只出现一次，与顶层 url/pdf_url 相同的链接省略。
- HF 关联模型和数据集是引用该论文的产物。列表维持上游预览内按 likes 排序最多三项，每项只保留 id、likes。
- metadata 省略 None、空字符串、空列表和空字典，保留 0 和 false；各来源引用数保持独立口径。raw_id、许可、BibTeX、分类层级与编码、完整版本历史及离线溯源信息不进入模型输入。


## PDF 补救与用户凭证

search、lookup、discover 对最终返回论文调用 `PaperPdfRecoverService.recover`，随后统一投影。顺序为顶层 PDF、provider 注册的 PDF（S2 优先 OpenAlex）、确定 arXiv ID 对应的标准 PDF、Unpaywall DOI 补救。已有明确链接不发 HEAD、不下载验证；没有 DOI 不请求 Unpaywall。

Unpaywall 仅取 `best_oa_location.url_for_pdf` 或其他 `oa_locations` 的 PDF，不使用落地页和 OA 状态。邮箱配置 `UNPAYWALL_EMAIL` 默认为 `jzsun24@m.fudan.edu.cn`，单次请求超时 5 秒、并发上限 4、不自动重试。独立 DOI 缓存的正 TTL 沿用论文缓存 TTL，确认无链接或 404 使用负 TTL；网络失败不写负缓存。补救失败保留论文并报告 `unpaywall` execution，结果只填顶层 `pdf_url`，不新增 metadata 命名空间。

HTTP BYOK 请求头为 `X-OpenAlex-Api-Key`、`X-HuggingFace-Api-Key`、`X-S2-Api-Key`。MCP 从 `com.wisepen/tool_config` 的 `openalex_api_key`、`huggingface_api_key`、`s2_api_key` 读取，不进入模型参数或请求 JSON。没有搜索 BYOK 时，本地结果仍可使用平台 key 富化。

## 验证

```powershell
uv run --no-sync python -m pytest tests -q
```

[数据基线](docs/data-baseline.md)记录输入及样本验证范围；[阶段验收记录](docs/p0-p1-acceptance.md)记录真实基础设施与全量导入结果。单元测试中的外部依赖替身不替代真实 ES 验收。

真实 Redis 队列验收使用独立随机命名空间，显式设置 `SCHOLAR_TEST_REDIS_URL` 后执行 `uv run --no-sync pytest tests/test_s2_queue.py -q`。未配置时队列验收跳过，不连接默认服务命名空间。
