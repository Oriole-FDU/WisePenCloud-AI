# 实验分支的启动与边界

本分支为 `experiment_multi_agent`，从已合并上游的 `dev/common@243f98ac` 创建。目标是用现有搜索能力研究多智能体，当前不实现
agent team 或 sub-agent。

## 业务服务边界

| 项目                                  | 本分支行为                                                     |
|---------------------------------------|----------------------------------------------------------------|
| Chat、MCP                             | 按原有方式启动，搜索入口保留                                   |
| RAG 业务服务                          | 不需要启动；当前 Chat/MCP 没有调用它的客户端路径               |
| Sandbox 业务服务                      | 默认不启动，也不发现其工具                                     |
| Nacos、Mongo、Redis、Kafka 等基础设施 | 正常使用，连接、启动及生命周期代码不变                         |
| Chat 长期记忆及其 Qdrant 等依赖       | 沿用原有 Agent 配置，不将其等同于 RAG 业务服务，不在本任务裁剪 |

根 `pyproject.toml` 继续保留完整 workspace。工作区成员存在、参与锁文件解析，与必须启动其业务服务是两件事；因此不删除
RAG/Sandbox 的源码或依赖声明。

## 两处实际行为调整

1. [Chat AppSettings](../../services/wisepen-chat-service/src/chat/core/config/app_settings.py) 增加
   `MCP_SANDBOX_ENABLED: bool = False`。[系统工具目录](../../services/wisepen-chat-service/src/chat/application/tools/core/mcp/system_mcp_tool_catalog.py)
   在服务发现之前检查它；关闭时仅加载 `wisepen-mcp-service`，保留 `system_default_web_search` 等系统工具，不请求 Sandbox。
2. [主 Compose](../../docker-compose-app.yml) 的 `sandbox-service` 归入 `sandbox` profile。默认服务集合为 `chat-service`、
   `mcp-service`；RAG 原本就不在该 Compose 中。

开关来自 **Chat 的 Nacos 业务配置**。已有配置不写该字段时默认为 `false`；若远端明确设置了 `true`，应在搜索实验前改为
`false`。`AppSettings` 是普通 Pydantic `BaseModel`，不能把同名 `.env` 变量当成业务配置覆盖机制。

## 本机启动

沿用当前 `.env` 和 Nacos 中的 `wisepen-chat-service-dev.yaml`、`wisepen-mcp-service-dev.yaml`，以及原有基础设施和模型配置。现有
`.run/wisepen-chat.run.xml`、`.run/wisepen-mcp.run.xml` 可以继续使用。

从项目根目录安装这两个服务的依赖；`--inexact` 允许它们共用已有虚拟环境而不互相卸载：

```powershell
uv sync --package wisepen-chat-service --inexact
uv sync --package wisepen-mcp-service --inexact
```

分别在两个终端从项目根目录启动：

```powershell
uv run --no-sync --package wisepen-mcp-service python -m wisepen_mcp.main
```

```powershell
uv run --no-sync --package wisepen-chat-service python -m chat.main
```

不执行 `rag.main` 或 `sandbox.main`。Chat 仍通过现有 Nacos 服务发现连接 MCP，不另建替代客户端或实验服务器。

容器启动沿用已有镜像变量、配置及外部 `wisepen-net` 网络：

```powershell
docker compose -f docker-compose-app.yml up -d --build
```

如以后需要重新接入 Sandbox，需要同时启动其 profile，并在 Chat 的 Nacos 配置中设置 `MCP_SANDBOX_ENABLED: true`；只启动容器不会自动修改
Chat 的发现开关。

## 搜索实验入口

默认搜索是已有 MCP 服务中的工具，当前没有新增一个独立的 Search 微服务：

```text
Chat 请求
  → ChatTurnCoordinator / ToolRegistry.derive
  → SystemMcpToolCatalog.load_system_tools
  → system_default_web_search
  → McpServiceClient → wisepen-mcp-service
  → PlatformSearchTool → DeepSeek 原生搜索
```

主要入口：

- [Chat 工具派生](../../services/wisepen-chat-service/src/chat/application/tools/core/registry.py)：请求级工具可见性和上下文。
- [内部 MCP 客户端](../../services/wisepen-chat-service/src/chat/service_client/mcp_service_client.py)：Nacos 寻址、认证和
  MCP 调用。
- [搜索工具注册](../../services/wisepen-mcp-service/src/wisepen_mcp/capabilities/web_search/__init__.py)：当前搜索
  Provider 的注册。
- [平台默认搜索](../../services/wisepen-mcp-service/src/wisepen_mcp/capabilities/web_search/providers/platform_default.py)
  ：服务端 `WEB_SEARCH_API_KEY` 和原生搜索调用。
- [搜索合同](../../services/wisepen-mcp-service/src/wisepen_mcp/capabilities/web_search/README.md)：参数、域名过滤与结果格式。

搜索密钥使用 MCP 的 Nacos 配置中的 `WEB_SEARCH_API_KEY`，继续遵守既有配置方式。默认搜索在 Chat 中的名称带 `system_` 前缀，在
MCP 中的原名是 `default_web_search`。

## 已完成验证

- `uv run --frozen --no-sync python -m pytest services/wisepen-chat-service/tests -q`：21 passed。
- `uv run --frozen --no-sync python -m pytest services/wisepen-mcp-service/tests/web_search -q`：99 passed。
-
新增的 [工具发现行为测试](../../services/wisepen-chat-service/tests/application/tools/mcp/test_system_mcp_tool_catalog.py)
验证：关闭时搜索工具存在且不访问 Sandbox；显式开启后同时暴露 Sandbox 工具。
- `docker compose -f docker-compose-app.yml config --services`：仅 Chat、MCP。
- 添加 `--profile sandbox` 后，Compose 服务集合恢复包含 Sandbox。

这些是自动化行为测试和配置解析；没有把它们表述为真实外部服务的端到端联调结果。本任务未启动服务进程或调用计费搜索接口。
