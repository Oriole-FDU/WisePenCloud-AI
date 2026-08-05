# MCP Tool 开发与迁移指南

本文约定 WisePen 中一个 tool 如何迁移为 MCP tool，以及如何新增一个 MCP
tool。目标不是把原有函数套一层远程调用，而是让变动频繁的工具实现独立部署，
同时保持 agent 看到的输入、输出和失败语义清晰稳定。

当前参考实现：`wisepen_mcp.capabilities.web_search`。

## 先确定边界

一个 MCP tool 的实现、私有协议适配、依赖装配和返回模型都属于
`wisepen-mcp-service`。`wisepen-chat-service` 只保留以下职责：

- 发现 MCP 暴露的工具，并决定哪些工具对 agent 可见；
- 声明执行策略，例如超时、风险等级、是否保存结果；
- 声明用户级工具配置，例如某个 provider 的 API key；
- 将一次调用所需的私密配置通过 MCP request metadata 传给服务。

chat 不应继续保留同一 tool 的本地实现、provider 客户端、HTTP session、
tool 单例或供应商 base URL。否则一次工具修改仍会迫使 chat 重部署，MCP 化只
剩下形式。

通用且不依赖 chat 的算法放在 `wisepen-common`，例如 chunker 和 ranking
框架。依赖 chat 配置或只服务于 chat 的组合预设，保留在
`chat/application/utils/`，不要为了共享而制造反向依赖。

## 迁移已有 chat tool

按从内到外的顺序迁移，避免新旧实现同时长期存在。

1. 找到原 tool 的真实执行链路：工具类、service、外部 client、返回模型、
   DI 注册、chat 的 tool registry 和测试。不要只移动最外层 tool 类。
2. 在 `wisepen_mcp/capabilities/<capability>/` 建立 capability。将工具执行
   服务、外部协议适配和它们实际消费的模型一起迁入。
3. 把纯通用实现迁到 `wisepen-common`，并将所有调用点改为 `common.*`。
   不要让 MCP import `chat.*`。
4. 用 Pydantic `BaseModel` 定义 agent 可见的返回模型。返回字段必须能帮助
   agent 决策或继续操作；不要把供应商 request id、调试统计或无消费者的审计
   字段带入输出。
5. 在 `tools.py` 用 `@mcp.tool` 注册每个 tool。tool 参数就是模型可见的
   输入契约，使用类型、范围和描述表达约束；不要把内部 client、用户 API key
   或隐式上下文做成模型参数。
6. 在 capability 注册函数中挂载 tool，并在
   `wisepen_mcp.capabilities.build_mcp_server()` 中注册 capability。
7. 在 MCP 的 container 中装配 capability service、HTTP client、provider
   factory 和算法 pipeline；将工具实现所需的服务地址配置迁到 MCP 的
   `AppSettings`。
8. 在 chat 的 `SystemMcpToolCatalog` 为 MCP tool 加 overlay，声明 agent
   可见性、风险、超时、结果持久化、失败文案，以及必要的 `ToolConfigSpec`。
9. 删除 chat 的旧 tool、DI provider、旧服务地址配置和旧测试；保留 chat 到
   MCP 的远程调用路径，不要出现本地与远程两套同名工具。

迁移完成后的依赖方向应当是：

```text
chat  -> MCP client -> wisepen-mcp-service -> wisepen-common
                                         \
                                          -> external provider
```

## 新增一个 MCP tool

新增 tool 不需要迁移步骤，但边界相同。

1. 选择现有 capability，或在 `capabilities/` 新建一个以业务能力命名的目录。
   不要按 HTTP 框架、数据库或供应商名称划 capability。
2. 先写 agent 输入与输出契约：参数应让模型能够一次调用完成任务；输出应说明
   结果是什么、如何继续使用。固定值集合使用 enum，长度和数量使用明确范围。
3. 编写 application service，负责输入归一化、业务编排和错误映射。外部 API
   的请求格式、鉴权头和响应解析放到 provider/client 适配层，不要散落在 tool
   函数中。
4. 定义 Pydantic 返回模型，并给输出字段写语义说明。模型应优先呈现证据、下一
   步可用的标识和用户可见结果，而不是内部状态。
5. 在 `tools.py` 注册薄的 FastMCP 函数：读取明确的输入，调用 service，直接
   返回返回模型。不要额外添加只转发参数的私有 helper；有实际边界含义的 helper
   例如私密 metadata 解析可以保留。
6. 在 `capabilities/__init__.py` 与 `main.py` 的 server 构建路径中接入。
7. 在 chat 的 system catalog 加策略 overlay。未加入 overlay 的内部 MCP tool
   不会对 agent 暴露。

## 私密配置和调用上下文

用户配置的 API key、token 等不能出现在 MCP tool 的参数 schema 中，也不能写
入 MCP 服务的环境配置。原因是 tool schema 会展示给模型，且服务环境变量不应
保存用户级密钥。

正确路径为：

```text
chat ToolConfig
  -> McpRemoteTool.execute(config=...)
  -> McpServiceClient.call_tool(meta={"wisepen/tool_config": ...})
  -> FastMCP Context.request_context.meta
  -> capability service
```

使用 `CommonConstants.MCP_TOOL_CONFIG_META_KEY` 作为 metadata 键。MCP tool
只从 `Context` 读取该配置，并且只取自己声明需要的值。禁止记录完整 config、
API key 或含密钥的请求头。

无用户级配置的 tool 不需要 metadata，也不应因此增加空参数。

## 返回与错误

FastMCP tool 直接返回 Pydantic 模型，使客户端获得 `structuredContent`。chat
的 MCP client 应直接返回该对象，不能再次 `json.dumps` 成字符串；统一的 tool
结果渲染层会负责最终 JSON 展示和空值清理。

错误需在 capability service 转换为稳定的领域错误码。区分至少三类：

- 输入或缺少配置：不可重试；
- 凭据无效：不可重试，提示用户修复配置；
- provider 网络不可用：可由上层按策略重试；
- provider 响应不合法或业务失败：不可把它伪装为空结果。

不要为了“返回成功”吞掉外部协议错误。只有明确允许降级的系统能力，才能将某
个 provider 失败转换为可解释的降级结果。

## 验证清单

完成一个 tool 后，至少验证以下内容：

- FastMCP `list_tools()` 能发现正确的名称、输入 schema 和 output schema；
- tool 参数没有泄漏 `Context`、API key 或内部依赖；
- 返回的是 Pydantic 输出模型，客户端拿到的是结构化对象；
- 私有配置只经 `_meta` 传递，且未出现在 schema、日志或输出中；
- 外部 provider 用 `httpx.MockTransport` 覆盖请求方法、鉴权、关键参数和响应
  映射；
- chat 的 system catalog 已显式声明该 tool 的策略与配置；
- chat 已删除迁移 tool 的本地注册和实现；
- `rg` 确认 MCP capability 没有 `chat.*` 依赖，且没有旧导入路径残留。

推荐的最小命令：

```powershell
uv run ruff check services/wisepen-mcp-service/src/wisepen_mcp/capabilities/<capability>
uv run pytest services/wisepen-mcp-service/tests/<capability> -q
uv run pytest services/wisepen-chat-service/src/chat/tests/mcp -q
```

对于真实第三方 provider，还要用脱敏的真实凭据做一次最小调用，确认文档与实现
一致；只记录状态、字段结构和结果数量，不记录密钥或完整敏感响应。
