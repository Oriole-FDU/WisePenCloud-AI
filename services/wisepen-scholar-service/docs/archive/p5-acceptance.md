# P5 论文发现验收

Application 的第三个用例通过 `FieldPolicy.discoverer` 选择发现能力。V1 为 CS 登记 HF，输入为 field、period、sort、limit，没有
query。HF 适配器可同时实现 enrich/discover，不要求每个 provider 实现所有能力。

HF 发现遵循[官方
`HfApi.list_daily_papers` 源码](https://raw.githubusercontent.com/huggingface/huggingface_hub/main/src/huggingface_hub/hf_api.py)
：构造参数，一次 GET `/api/daily_papers`，检查 HTTP 状态，逐条转换响应。使用原生异步 `httpx.AsyncClient`，不引入同步 SDK
或线程包装。

- 默认 trending；recent 对应 `publishedAt`，trending 原样传递。
- daily 不传 date，由上游返回最近内容，与官方 SDK 默认行为一致；weekly 传当前 UTC ISO 周，monthly 传当前 UTC 月。
- 成功响应按上游顺序交付，保留实际返回的论文及其日期。缺少渠道日期、日期跨度或时间窗口不作为丢弃响应、过滤论文或抛错的理由。不探测首条日期，不发第二次列表请求，不额外逐篇富化。
- `window_start/window_end` 仅说明显式请求的周/月范围；daily 使用上游默认，因此为空。它们不宣称每篇返回论文都落在该范围内。
- HTTP/网络、解析、缓存错误及取消保留真实失败语义，不改为空列表或失败分类；上游正常返回空列表就是成功的空结果。

缓存只存成功候选和请求范围，默认 15 分钟；每次重建 execution。相关测试覆盖官方请求参数、单次列表请求、成功响应的保序交付、正常空列表、异步调用以及既有
HTTP/MCP 合同。异常日志保留错误与回溯，关闭局部变量展开，避免 BYOK 密钥随栈输出。

38 项相关测试通过；真实 HTTP 的 daily/weekly/monthly trending 均成功返回论文，重复请求命中缓存，公共 MCP ClientSession 经
Nacos 调用成功。记录保存在被忽略的 `.runtime/p5-real-acceptance.json`
。此前因自行日期校验而主动丢弃成功响应的记录不再作为当前实现的验收；本轮按官方流程重新记录结果，不再调查或阻断上游排名与日期的关系。
