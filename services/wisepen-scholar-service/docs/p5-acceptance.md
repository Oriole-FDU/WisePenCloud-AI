# P5 论文发现验收

`PaperDiscoveryService` 是 application 的第三个用例，按 `FieldPolicy.discoverer` 选择通用发现能力。V1 登记 CS 的 HF 渠道，输入只有 field、period、sort、limit，没有 query。HF 适配器同时具备 enrich/discover；不强制每个 provider 实现全部能力。

发现直接保留渠道列表实际返回的论文与研究产物信号，不再额外逐篇富化。缓存只存成功的候选和实际窗口，默认 15 分钟；本次 execution 每次重建。发现单源错误、缓存错误与取消直接传播，不转成失败分类或空列表。

- daily 先按 publishedAt 取最新一条确定分发日，再携带 date 请求当天列表；weekly/monthly 对应当前 UTC 自然周、自然月。只用 `submittedOnDailyAt` 判断分发日，每条结果都必须在请求窗口内，缺失或越窗直接失败。
- 默认 recent，映射上游 publishedAt。实测 trending 忽略 date/week/month 并返回同一全局列表，因此保留参数枚举但明确拒绝该排序，不把全局榜单称为周期趋势，也不静默回退。
- HF provider 23 项、发现与 HTTP 合同 21 项、日志秘密局部变量保护 1 项，共 45 项通过，覆盖真实嵌套响应形状、渠道日期、两步 daily 请求、周/月越窗失败与 trending 拒绝。Scholar 的异常日志保留错误与回溯，关闭局部变量展开，避免 BYOK 密钥随栈输出。
- MCP Scholar 工具测试 9 项、Chat 工具配置测试 3 项通过，包含既有 search/lookup；discover schema 没有 query 或 key，Chat 调用不要求密钥配置。
- `.runtime/p5-real-acceptance.json` 验证真实 HTTP：daily 为 2026-10-27；weekly 为 2026-10-26 至 11-01；monthly 为 2026-10-01 至 10-31。三个窗口均逐篇核对实际分发日期，重复请求缓存命中；trending 请求返回 503 原始能力错误。公共 MCP ClientSession 经 Nacos 调用成功，schema 默认 recent 且没有 query/key。

协议依据为 [HF 官方 API 文档](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api)，上游参数支持不等于真实排序组合生效；本轮以原始响应实际日期验收。HF 接口有过一次瞬时 503，失败直接向外传播，没有写入失败缓存；后续重跑完成。

渠道结果是否新近以实际返回窗口判断，不把最近可用 daily 内容标成今天发布。上述真实记录保留本轮窗口与数量；不将动态渠道数据写进长期测试断言。
