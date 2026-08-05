# MCP 中的用户工具配置传递

```
用户保存配置
  -> chat 侧 ToolConfig / secret_config 分开存
  -> ToolRegistry._resolve_tool_config() 合并成运行时 config
  -> ToolExecutor 把 config 交给 tool.execute(...)
  -> McpRemoteTool.execute(config=...)
  -> McpServiceClient.call_tool(meta={"wisepen/tool_config": ...})
  -> FastMCP Context.request_context.meta
  -> capability 内部 get_tool_config_value()
  -> service/provider 用这个密钥发外部请求
```

关键点有两个。

第一，密钥不进 LLM 可见的 tool 参数。`ToolConfigSpec` 只负责声明哪些字段是 `secret_keys`，chat 的 `/updateUserToolConfig` 也把 `config` 和 `secret_config` 分开校验、分开落库，列表/详情接口只返回 `secret_fingerprints`，不回传明文。

第二，运行时只在服务内部传递。`ToolExecutor` 从仓库里取出配置后，传给 `McpRemoteTool.execute(config=...)`；`McpServiceClient.call_tool()` 再把它塞进 MCP 请求的 `meta`，服务端用 `get_tool_config_value()` 从 `Context.request_context.meta` 里读出来。`web_search` 这条具体链路里，`api_key` 只在 MCP 服务内部变成 provider 请求头，没出现在 tool schema 里。
