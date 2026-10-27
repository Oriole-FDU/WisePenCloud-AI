# P4 Lookup purpose 与 S2 富化验收

P4 将 S2 富化接入既有 `PaperLookupService`，没有新增独立 application service。`lookup/enrichment.py` 只承担本用例的缓存、S2 批次、身份关联和并发协调。Provider 继续只实现通用 `PaperEnricher`，离线 dataset API 留在 ETL。

- `details` 只读取已有本地事实、允许的 OpenAlex 精确详情与公共缓存，不在线调用 S2；完整缓存 Paper 可满足 S2/Corpus ID 的详情查询。
- `enrich` 输出身份、精选 metadata 和富化完成情况，不返回完整 Paper，也不调用 OpenAlex 详情。已有真实 S2 事实时可保留部分结果；只有基础 Paper、没有富化事实且 S2 失败时直接抛出原异常。
- `full` 返回可用详情，并按核心信号缺失情况补充 S2。已有基础论文不会因可选富化失败而丢失；没有任何可用事实且请求失败时直接抛错。
- 核心信号按 citation_count、influential_citation_count 与 TLDR text 判断，`observed_at` 不代表字段齐全。0、false、空字符串保持原值。成功确认的空结果或缺字段缓存 1 小时，完整正常结果缓存 24 小时；错误不写成未找到。
- 响应身份冲突不写 aliases；共享在途请求保留各请求的身份约束，最后一个等待者取消时取消底层 I/O。程序错误和取消不被改写成部分失败。

## 行为与真实边界

公开 lookup 行为测试 42 项、S2 协议测试 3 项、HTTP 测试 12 项通过。HTTP 覆盖已有 Paper 时保留基础事实及真实错误文本、无 Paper 时抛错映射为 503；缓存、TTL、重复输入、身份冲突与并发取消经公开 lookup 或 Redis adapter 边界验证。

`.runtime/p4-fixture-acceptance.json` 使用用户保存的三项真实 S2 响应作为 HTTP 边界替身，连接真实 ES/Redis：三篇均找到，首次 2 个字段分组批次，重复调用 0 次 S2，Corpus ID 的 details 使用完整缓存。此项证明真实数据投影与存储，不能单独证明联网 S2 成功。

`.runtime/p4-http-real.json` 记录正式 HTTP/Graph 请求：首次 full 已有基础论文且 S2 429，保留基础论文和真实错误；无基础事实的 Corpus ID full 收到 503 和真实 429 文本；随后 MemGPT enrich 的 Graph 请求成功，输出 metadata 且不返回完整 Paper。

`.runtime/p4-selected-fields-real.json` 记录成功请求后从共享缓存读取的 MemGPT：citation_count=1705、influential_citation_count=140、TLDR 长度 266，PDF 可用、BibTeX 长度 306。没有 embedding、作者画像或其他未选择字段。

`.runtime/p4_s2_boundary_acceptance.json` 验证两个独立进程使用真实 Redis 共发出 4 次请求，最小发送间隔 1007.2ms，均不发送 User-Agent。该独立协议批次当时收到真实 429；没有自动重试、删除配额 key 或绕过限速。
