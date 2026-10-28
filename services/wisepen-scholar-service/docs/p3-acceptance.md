# P3 验收记录

> 2026-10-27：P3 已完成定向行为及真实链路验收。P4/P5/P6 继续按各阶段单独交付。

## 范围

P3 覆盖 OpenAlex BYOK 检索与精确 details lookup、策略驱动的默认/多源路由、跨来源身份融合，以及现有 Chat/MCP 凭证通道。本阶段不代表
P4 S2 purpose 富化、P5 论文发现或 P6 同步和部署已完成。

## 当前边界

- `application/search/` 与 `application/lookup/` 是两个用例包。候选后处理属于 search 内部；按 purpose 执行的 S2 富化属于
  lookup 内部；不设置独立的 candidate/enrichment 应用服务。
- 共享 `FieldPolicy` 声明 `default_search_plan`、`without_credentials_plan`、`multi_source_plan`、`search_enrichers` 和
  `discoverer`。容器按开放来源名绑定 provider 能力。`Paper.source` 是开放字符串，来源事实保存在
  `Paper.metadata[provider_name]`。
- Provider 依赖通用 `PaperSearcher`、`PaperEnricher`、`PaperDiscoverer` 能力端口；一个 provider 可实现多种能力。领域到来源的字段映射仍集中在
  `providers/field_mapping.py`。
- 已知 provider 外部 I/O 错误使用 `ProviderError`，保留原异常文本和 cause，不使用 reason/status 分类字段。若 HTTPX timeout
  文本缺少请求 URL，适配器补上 URL，不读取请求头。程序错误和取消继续传播。
- 单来源搜索失败直接传播。多源至少一个来源成功才返回部分结果；所有来源都失败时传播 provider 错误。Lookup
  批次只有存在其他可用项时才保留逐项失败。可选搜索后处理失败可保留基础论文并报告 provider 错误文本。
- CS 搜索后处理已使用异步 HF HTTP provider。公开 `discover_papers` 接线及其 P5 验收待完成。

## 行为证据

以下测试覆盖 P3 的行为边界：

| 测试文件                             | 覆盖的可观察行为                                                                                                                                                          |
|--------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `tests/test_multisource_search.py`   | 默认与无凭证路由、OpenAlex 跨凭证及无凭证缓存复用、精确身份融合及冲突、多源部分失败、最终 limit 后富化；医疗 mock 策略注册 PubMed、Web of Science 与专有富化器，不改核心搜索流程。 |
| `tests/test_openalex_lookup.py`      | 本地优先精确查询、重复项与顺序、歧义和未找到、BYOK 要求、批次部分成功、全失败传播、程序错误和取消传播。                                                                   |
| `tests/test_candidate_enrichment.py` | 可选富化发生 provider I/O 失败时保留基础论文；单源及全来源搜索失败传播。                                                                                                  |
| `tests/test_openalex_provider.py`    | OpenAlex 请求映射、用户级 Bearer 凭证、精确标识查询，以及外部 HTTP/网络错误的 cause 保留。                                                                                |

PubMed/Web of Science 用例是 mock 能力与配置扩展测试，不代表已实现真实 provider 或通过真实外部 API 验收。

## 最终验收

- Scholar：
  `uv run --no-sync python -m pytest tests/test_candidate_enrichment.py tests/test_multisource_search.py tests/test_openalex_lookup.py tests/test_openalex_provider.py tests/test_huggingface_provider.py tests/test_http_contracts.py -q`
  ，71 项通过。
- MCP：`tests/scholar/test_scholar_tools.py`，8 项通过，包括真实 ClientSession 对 503、429、timeout 错误文本的可见性。HTTP 整次
  lookup 失败返回 503 与真实错误文本；部分 msearch 失败保留对应项及成功论文。
- Chat：本阶段的工具配置相关测试 24 项通过；验证可选 OpenAlex 配置、必填配置判断、工具禁用和恢复时读取当前配置，挂起记录不持久保存密钥。
- 实际 OpenAlex、ES、Redis、Nacos 与 MCP 验收记录：`.runtime/p3-real-acceptance.json`。默认 OpenAlex 检索返回 3 篇且重复命中缓存；CS
  多源返回 10 篇，MemGPT 精确合并为 1 篇，保留 arXiv 基础事实和 OpenAlex metadata；OpenAlex ID lookup 成功；无密钥
  multi_source 返回能力不可用；MCP metadata 经 header、Nacos 路由完成真实调用。
- 同一次 CS 搜索已确认 MemGPT 的 HF upvotes 信号与 `enrichment_execution`，使用原生异步 HTTP 和公共 Redis 缓存。HF token
  只由 Nacos 注入适配器；用户 OpenAlex 密钥仅在当前 HTTP header 传递，不进入模型参数、URL 或缓存明文。
- 当前开发机验收使用进程内网络中继和代理环境，未将该网络规避方式写入产品代码。
- Common RpcClient 保持既有机制：5xx 响应正文只保留前 200 字符。MCP 可见的错误文本受此长度限制；本阶段没有修改 Common
  跨服务策略。
