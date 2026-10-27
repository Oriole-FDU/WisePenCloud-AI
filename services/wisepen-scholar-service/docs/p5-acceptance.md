# P5 论文发现验收

`PaperDiscoveryService` 是 application 的第三个用例，按 `FieldPolicy.discoverer` 选择通用发现能力。V1 登记 CS 的 HF 渠道，输入只有 field、period、sort、limit，没有 query。HF 适配器同时具备 enrich/discover；不强制每个 provider 实现全部能力。

发现直接保留渠道列表实际返回的论文与研究产物信号，不再额外逐篇富化。缓存只存成功的候选和实际窗口，默认 15 分钟；本次 execution 每次重建。发现单源错误、缓存错误与取消直接传播，不转成失败分类或空列表。

- daily 先按 publishedAt 取最新一条确定分发日，再携带 date 请求当天列表；weekly/monthly 对应当前 UTC 自然周、自然月。只用 `submittedOnDailyAt` 判断分发日，每条结果都必须在请求窗口内，缺失或越窗直接失败。
- 默认 trending；recent 映射上游 publishedAt，trending 与周期参数一起透传。此前把实测异常写成“不支持”并在请求前拒绝的做法已撤销；客户端不擅自禁用官方参数组合。
- HF provider、发现与 HTTP 合同共 46 项通过，覆盖真实嵌套响应形状、渠道日期、两步 daily 请求、有效周期与 trending 组合、周/月实际越窗失败。日志秘密局部变量保护测试已通过；异常日志保留错误与回溯，关闭局部变量展开，避免 BYOK 密钥随栈输出。
- MCP Scholar 工具测试 9 项、Chat 工具配置测试 3 项通过，包含既有 search/lookup；discover schema 没有 query 或 key，Chat 调用不要求密钥配置。
- `.runtime/p5-real-acceptance.json` 已在恢复官方参数组合后重新运行：recent 的 daily 为 2026-10-27；weekly 为 2026-10-26 至 11-01；monthly 为 2026-10-01 至 10-31。三个窗口逐篇核对实际分发日期，重复请求缓存命中；公共 MCP ClientSession 经 Nacos 调用成功，没有 query/key。trending 实际发出请求后返回窗口外论文，记录为 `upstream_window_mismatch`，没有沿用此前主动拒绝请求的输出作为验收。

协议依据为 [HF 官方 HfApi 文档](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api#huggingface_hub.HfApi.list_daily_papers) 与 [官方 CLI 实现](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/cli/papers.py)。二者同时暴露 date/week/month 及 sort=trending，CLI 也一起转发，没有互斥限制。已合并的[官方 PR #3585](https://github.com/huggingface/huggingface_hub/pull/3585/files)还直接测试 `date="2025-10-29", sort="trending"` 并检查 upvotes 降序，但该测试没有断言返回论文仍在请求日期内。没有找到“官方不支持周期内 trending”的声明。

[官网全局 Trending 页面](https://huggingface.co/papers/trending)和 API 的周期排序应区分：本轮读取的官网前端中，切换 Daily/Weekly/Monthly 会导航到对应日期、周、月页面并退出全局 Trending 模式。未找到能解决本轮 JSON API 窗口异常的另一组官方参数；不据此伪造周期内榜单，也不将全局榜单标为当天结果。

合法参数原始对照见 `.runtime/hf-period-trending-raw.json`；绕过本机代理的重复对照见 `.runtime/hf-direct-period-compare.json`。后者中 `date=2026-10-08&sort=publishedAt&limit=2` 返回的分发日均为 10-08，而同日 trending 返回一篇分发日为 10-14 的论文和一篇缺少分发日的论文；自然周 trending 也出现相同返回。它们证明本轮请求存在窗口异常，不能证明该能力永久不受支持。trending 的真实成功验收仍未完成；实际越窗继续按上游响应异常直接抛出，不静默更换排序。

HF 接口曾有一次瞬时 503，失败直接向外传播，没有写入失败缓存；recent 后续重跑成功。

渠道结果是否新近以实际返回窗口判断，不把最近可用 daily 内容标成今天发布。上述真实记录保留本轮窗口与数量；不将动态渠道数据写进长期测试断言。
