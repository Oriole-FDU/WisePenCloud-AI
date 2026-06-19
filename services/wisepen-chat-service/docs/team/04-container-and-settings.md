# Container 与 Settings 边界规范

本文约束依赖注入容器、应用配置和工具配置的职责边界。

## Container 注册原则

只有满足以下原因之一，才应注册到 `src/chat/container.py`：

- 拥有需要生命周期管理的资源，例如 HTTP client、Redis、Kafka、RPC、连接池、文件加载器。
- 重复创建成本高，例如模型运行时、重型本地索引、持久缓存。
- 是应用级共享协调器，例如 `ToolRegistry`、`ToolDispatcher`、`ToolContentStore`。
- 是需要进入全局 `ToolRegistry` 的 tool 实例。

不应注册：

- 普通配置值。
- 轻量无状态 renderer/converter。
- 每请求的 plan、candidate、request model、parse result。
- 不拥有资源且不作为应用入口的 helper service。
- 小 helper 函数或纯工具类。

## HTTP Client 规则

HTTP client 必须按外部集成命名，不能使用笼统名称。

推荐：

- `paddle_ocr_http_client`
- `asset_download_http_client`
- `search_api_http_client`

禁止：

- `tool_http_client`
- `common_http_client`
- `http_client_for_tools`

异步请求路径使用 `httpx.AsyncClient`。拥有连接池的 integration client 不应自行创建 HTTP client，应由 container 注入。

## Settings 边界

`app_settings` 承载全局应用和基础设施配置：

- Redis、Mongo、Kafka、RPC、Nacos、OSS 等连接信息。
- LLM、embedding、reranker 等全局模型配置。
- 全局限制，例如 `TOOL_RESULT_MAX_CHARS`。
- 服务发现、部署、运行期配置。

`tool_settings` 只承载工具专属行为参数：

- 单个工具的 timeout。
- 单个工具的 retry。
- 只影响工具行为的解析阈值或策略开关。

如果同一配置同时出现在 `app_settings` 和 `tool_settings`，说明边界错误，应删除重复项并重新归类。

## Nacos 规则

当前服务保持 `container` 作为唯一运行入口。

不得为了 `tool_settings` 增加第二套 Nacos 加载路径，除非服务先完成清晰的多源配置设计。

`tool_settings` 可以暴露模块级实例：

```python
tool_settings = ToolSettings()
```

但不能创建新的容器、bootstrap 流程或运行入口。

## Review 清单

新增 provider 前必须回答：

- 这个对象拥有哪个资源。
- shutdown 时是否需要关闭或 flush。
- 单例避免了什么具体成本。
- 为什么 direct constructor 或模块级 cache 不够。
- provider 名称是否绑定具体集成。

新增 setting 前必须回答：

- 它是全局基础设施配置，还是某个工具的行为参数。
- 是否复制了已有 setting。
- 是否让 `tool_settings` 变成了 `app_settings` facade。
