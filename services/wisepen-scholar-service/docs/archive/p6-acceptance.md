# P6 验收记录（未完成）

截至 2026-10-27。此记录区分隔离测试、真实供应商观察与部署状态，不代表 P6 已完成。

## 已通过的合同测试

运行命令：

```sh
uv run --no-sync pytest tests/test_arxiv_oai.py tests/test_arxiv_sync.py tests/test_s2_diff.py tests/test_cli.py tests/test_etl_contracts.py tests/test_s2_etl.py --basetemp .runtime/p6-final-suite
```

结果：44 项通过。测试使用临时 DuckDB、样本 gzip/JSONL 和 HTTP/ES 替身，覆盖 OAI 每页 3 秒限速、分页与游标提交边界、arXiv
同步重放、连续 S2 diff、文件失败恢复、无更新 release 的发布确认、全量及增量索引/删除、删除失败保留与重放、带未发布删除的基线拒绝、CLI
release 参数，以及原 S2 离线导入行为。它们不证明真实供应商同步或全库导入完成。

最终 Scholar 全套测试 165 项通过；sdist/wheel 构建成功，wheel 包含三个用例及离线同步模块，不含 `.env`、DuckDB 或第三方数据。

## 真实外部观察

- OAI `Identify` 成功，HTTP 状态为 200，响应日期为 `2026-10-14T11:08:16Z`，粒度为 `YYYY-MM-DD`；记录见被忽略的
  `.runtime/p6-oai-identify.json`。
- 经代理的真实 OAI `ListRecords` 请求在 TLS EOF 处中断；直连（`trust_env=false`）首个页面成功，HTTP 状态为 200，返回
  `2026-10-27T02:04:23Z`、1,300 条记录及后续页 token。直连证据见被忽略的 `.runtime/p6-oai-direct.json`。失败的代理同步未推进
  arXiv 游标。
- 随后通过进程 `NO_PROXY` 指定 OAI 直连，真实 harvester 完整翻页并解析 21,712 条活跃记录，末页日期为
  `2026-10-27T02:18:55Z`，下一轮日期为 `2026-10-27`；没有删除记录。来源分页及原始投影解析已通过，此探测不修改数据库或提交游标，见
  `.runtime/p6-oai-harvest.json`。
- 真实 S2 Datasets API `release("latest")` 成功返回 `2026-10-20`；响应记录见被忽略的 `.runtime/p6-real-release.json`。P2 的
  90 个分片已全部读取；首次全量投影内存不足并回滚，正在有界重跑，完整基线及其发布未确认，因此尚未运行真实 S2 release diff。
- arXiv 官方条款要求 Legacy API（含 OAI-PMH）所有自有机器合计每 3
  秒最多一个请求，并限制同时只有一个连接（[Terms of Use, Rate limits](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/tou.md#L205-L211)
  ）。OAI harvester 已按页串行复用客户端，并在每次请求前等待 3 秒。

## 构建与部署

本次只交付 Compose/Jenkins/systemd 配置和运行说明。Compose 配置解析、YAML 校验和 Python 包构建已通过，构建上下文排除原始数据、运行产物与凭证。Docker
未启动，镜像未构建，生产部署和定时任务安装均不执行；它们不属于本次完成门槛。

## 待完成

- P2 全量 papers/tldrs 分片读取已完成；继续完成 S2 信息合并、关联统计与发布，并核对 DuckDB/ES 样本。
- 在真实 OAI `ListRecords` 成功后完成一次同步，确认发布与缓存失效成功后才推进游标。
- 以已验证的完整 S2 release 为 baseline 执行真实连续 diff，验收更新/删除、ES 发布、缓存失效和 completed release 推进。
