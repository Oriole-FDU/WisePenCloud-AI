# WisePen Scholar Service

Scholar 是内部 HTTP 微服务。MCP 工具由现有 `wisepen-mcp-service` 注册，Scholar 不启动独立 MCP server。

## 结构与迭代边界

- `api/schemas` 在 HTTP 边界校验完整契约；`api/endpoints` 只做 DTO 转换、预算控制及容器注入。
- `application/search/`、`application/lookup/` 分别拥有检索和精确查询用例；候选后处理属于 search 内部，S2 在线富化的消费边界属于 lookup 内部。没有独立的 candidate/enrichment 应用服务。`field_policies.py` 是两者共享的领域策略表。
- `domain/models/paper.py`、`identifiers.py` 保存领域论文及身份；请求、结果、执行状态属于具体应用用例或共享执行模型。`Paper.source` 接受开放来源字符串，来源附加事实放在 `metadata[来源名]` 自由字典中，不依赖供应商专用领域类型。
- `domain/interfaces` 按搜索、富化、发现能力声明通用端口；provider 可实现一个或多个能力。`providers/field_mapping.py` 仍是领域分类到 arXiv/OpenAlex 来源参数的统一映射文件。
- `core/storage` 实现 ES 论文读模型和 Redis 查询缓存；`container.py` 装配依赖。
- `core/config` 与 Chat/Sandbox 一样，通过 Bootstrap 定位 Nacos，再由 AppSettings 加载业务字段；`main.py` 负责日志、观测、安全中间件、客户端生命周期和服务注册。
- `etl` 独立维护 DuckDB 来源事实及发布进度；HTTP 不读写 DuckDB，不启动 ETL。

P1 本地 arXiv 闭环、P3 OpenAlex BYOK 与配置驱动检索已验收；P2 真实全量 S2 导入仍在后台进行。CS 搜索后处理通过原生异步 HTTP 调用 HF，lookup 的 S2 purpose 富化按 P4 验收。公开论文发现属于 P5。

领域策略声明默认来源、无凭证计划、多源计划、搜索后处理器及发现器；来源绑定由容器注册。单源搜索或所有来源都失败时传播 provider 错误；多源仅在至少一个来源成功时返回部分结果，并保留失败来源错误文本。lookup 批次只有在存在其他可用项时才以逐项状态保留 provider 失败；可选搜索后处理失败保留基础论文及错误文本。程序错误和取消不会降级为来源部分失败。

## 配置与启动

在本目录复制 `.env.example` 为 `.env`，填写现有 Nacos 引导凭证。业务配置使用 DEFAULT_GROUP 下的 `wisepen-scholar-service-dev.yaml`，字段见 [配置模板](docs/platform-settings.yaml)。prod 对应 `wisepen-scholar-service-prod.yaml`。不把凭证提交进仓库。

在本目录执行：

```powershell
uv sync --inexact --package wisepen-scholar-service --extra test
uv run --no-sync uvicorn scholar.main:app --host 127.0.0.1 --port 19916
```

`GET /scholar/health` 检查真实 ES、Redis 及已发布别名，未就绪返回 503。与业务 API 一样，需要现有内部 `X-From-Source` 请求头。

## 初始化和恢复

```powershell
uv run --no-sync scholar-etl import-arxiv
uv run --no-sync scholar-etl status
uv run --no-sync scholar-etl publish
```

默认输入 `third_party_data/arxiv-metadata-oai-snapshot.json`，数据库 `.runtime/scholar.duckdb`。输入中的重复 arXiv ID 按最新 `update_date` 保留，同日取文件中后出现的记录。`--source`、`--db`、`--batch-size` 支持显式覆盖。`publish --alias` 可指定独立验收别名；正式别名默认 `wisepen-scholar-arxiv`。

导入按批次同时提交原始记录、规范投影、待发布 ID 和字节游标；直接重跑从 checkpoint 继续。相同输入重复运行不增加论文；源文件路径、大小或修改时间变化时拒绝复用原数据库，重建应指定新的 `--db`。DuckDB 文件锁保证单写者，不同时运行两个 ETL 写进程。

`--max-batches N` 限制本次工作量，适合验证恢复；未读到 EOF 的导入不能发布。发布逐项确认 Bulk，成功项移出待发布集合，失败项重放同一文档 ID。全部文档数量及内容抽样检查通过后，才原子切换版本索引别名。旧索引不自动删除。在线请求总预算默认 25 秒；独立 ETL 的单次存储请求超时默认 120 秒，通过 `SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS` 配置。资源有限的 ES 实例可以用 `publish --batch-size 1000` 减少单次写入压力。

## HTTP 与 MCP

Lookup 的 `details/enrich/full` 已接入同一个用例。details 可复用缓存且不在线访问 S2；enrich 只输出精选信号；full 同时返回可用基础论文。没有可用结果的外部失败直接抛出，已有论文或信号的部分结果保留真实错误。缓存与真实边界验收见 [P4 记录](docs/p4-acceptance.md)。

`POST /scholar/papers/search` 接受 query、field、published_after/before、limit、source_scope。时间按首次提交日期包含两端过滤；领域采用 plan.md 的 arXiv 分类映射。查询缓存默认 10 分钟，缓存只存论文事实，每次重新构造执行情况。

`POST /scholar/papers/lookup` 接受 identifiers、purpose，逐项保留输入顺序、重复输入、未找到、标识错误或 DOI 歧义。arXiv 版本和官方 URL 归一化，DOI 不区分大小写。没有本地来源映射的领域不会退回全库检索。所有业务响应使用 Common `R<T>`。

现有 MCP 服务的 `search_papers`、`lookup_papers` 使用 Nacos RpcClient 调用这些 HTTP 接口，返回解包后的 data；来源路由和论文归一化不复制到 MCP。阶段能力和验收进度见 [P3 验收草稿](docs/p3-acceptance.md)，最终 root 验收结果尚待补录。

## 验证

```powershell
uv run --no-sync python -m pytest tests -q
```

[数据基线](docs/data-baseline.md)记录输入及样本验证范围；[阶段验收记录](docs/p0-p1-acceptance.md)记录真实基础设施与全量导入结果。单元测试中的外部依赖替身不替代真实 ES 验收。
