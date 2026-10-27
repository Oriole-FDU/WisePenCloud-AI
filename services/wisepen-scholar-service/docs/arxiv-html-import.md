# arXiv HTML 正文离线入库

在服务目录用现有看板包装器启动或续跑：

```powershell
uv run --no-sync python scripts/offline_dashboard.py run --name "arXiv HTML 正文：抓取、清洗、入库" arxiv-html -- uv run --no-sync scholar-etl import-arxiv-html --batch-size 20
```

看板地址为 <http://127.0.0.1:19917>。如未启动，另开终端执行 `uv run --no-sync python scripts/offline_dashboard.py serve`。单独试跑一批可增加 `--max-batches 1`；同一个命令重新运行即可续跑。

## 数据处理

每个 ID 只请求 `https://arxiv.org/html/{arxiv_id}{latest_version}`，使用本地元数据记录的最新版本。数字 ID 从新到旧处理，之后处理旧式分类 ID；不按年份猜测是否有 HTML。[arXiv 正在逐步补齐历史 HTML](https://info.arxiv.org/about/accessible_HTML.html)，可用范围以实际响应为准。

清洗规则来自用户提供的 `test_arxiv_html.py`，正式入口位于 `scholar.etl.arxiv_html_cleaner`。沿用 DOM 去噪、字符清理和至少 500 字符正文门槛，不增加语义清洗、重写或模型调用。只保存 `body`，不使用 HTML 覆写标题、摘要等元数据，也不重复索引这些段落。

HTTP 404/410、清洗结果为空时记录“无可用正文”，静默跳过；只有看板聚合计数，不逐篇报错。不尝试 PDF、源码、其他域名或额外下载。403、429、5xx、网络异常及程序错误直接抛出，不误记成“无 HTML”。

任务以单连接串行请求，起点间隔默认 3 秒，可用 `--request-interval` 调大。默认全库排队，先处理新论文；3,224,542 篇逐篇检查仅等待间隔的理论下限约 112 天，网络与下载还会增加耗时。结果按批次逐步生效，不等待全部结束。

## 持久化与恢复

DuckDB `arxiv_html` 只保存 ID、已检查版本和 `pending/available/unavailable` 进度。正文作为 `arxiv.document.full_text` 持久化，与进度及 `pending_publish` 在同一事务提交。每 20 篇调用既有发布器，将完整论文文档写入当前 ES 写入目标、刷新并失效本服务查询缓存。

中断后重跑先发布已保存但尚未写入 ES 的正文，再继续未检查版本；不会重下已成功或已确认无 HTML 的同版本。元数据更新保留同一最新版本的正文，出现新版本时去掉旧正文，后续任务重新检查新版。S2 富化仍只更新自己的命名空间，保留正文。

任务运行期间持有 DuckDB 写连接，应与其他 ETL 串行运行。要运行其他离线写任务，应先停止当前 HTML 命令，之后按同一命令恢复。没有添加 HTTP 后台抓取或供应商接口。

## 索引和结果

现有索引追加使用 `english` analyzer 的 `full_text` 字段即可，不执行整库 reindex。新增正文通过原有待发布链路逐篇补齐；无正文文档仍可检索。

统一 `combined_fields` 进行 BM25F 近似召回，标题/TLDR/摘要/正文初始权重为 **9:6:3:1**，保留原词形与标题/摘要短语加分、类别和日期过滤。不使用作者或引用数重排。查询词可横跨元数据和正文，正文单独命中也可召回；这些权重是初始先验，并非评测所得最优参数。

正文不进入 `Paper` 或 HTTP/MCP 模型。ES search 和 lookup 均排除 `_source.full_text`，不做原文片段或 highlight；用户仍只看到元数据、原始论文 URL 和 PDF URL。
