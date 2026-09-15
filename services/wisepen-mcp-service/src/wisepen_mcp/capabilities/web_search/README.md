# WisePen MCP Service

Internal MCP service for AI-safe Wisepen capability tools.

# 平台默认 Web Search

`default_web_search` 使用平台托管的智谱 GLM Web Search，保留原工具名和注册方式。MCP 输入包含必填 `query`、可选 `max_results`（默认 10，范围 1–20）和 `recency`，不暴露 `mode`、`focus` 或用户 API key。

在 MCP 服务的 Nacos 应用配置（`wisepen-mcp-service-dev.yaml` / `wisepen-mcp-service-prod.yaml`）中配置 `WEB_SEARCH_API_KEY`。默认空值会在请求前返回配置缺失错误；请求侧 key 不会覆盖平台密钥。`WEB_SEARCH_GLM_BASE_URL` 默认为 `https://open.bigmodel.cn/api/paas/v4`。

GLM 请求固定使用 `search_std`、`content_size="medium"`、`search_intent=False`。Adapter 将查询截取至 70 字符，公共结果的 `query` 仍保留规范化后的完整查询。每条网页的 `content` 映射为摘要证据 `evidences`，不执行全文抓取，也不生成顶层 `summary`。

# Web Search 新鲜度

`recency` 仅接受 `day/week/month/year`，省略或 `null` 表示不限。它是软新鲜度意图，具体日期边界由 Provider 映射；精确日期要求由调用方写入 `query`，基座不解析查询、不做日期后过滤。

GLM、Exa、Tavily、Firecrawl、百度千帆暴露完整四档。TinyFish、AnySearch 因当前搜索路径无法统一实现四档而不暴露。是否暴露由方法签名推导；拥有独立 academic 路径的工具必须两个方法均声明 `recency`。

Exa 以 UTC 当前时刻回溯 1/7/30/365 天，过滤发表时间；Firecrawl 网页使用原生 `qdr:d/w/m/y`，学术路径按 UTC 日期包含首尾共 1/7/30/365 个自然日。百度 `day` 使用北京时间当天的显式日期上下限：2026-09-15 实测 `now+1d/d` 被拒绝，同一 `YYYY-MM-DD` 的 `gte/lte` 则返回当日多个非零点结果，历史单日查询亦通过。

# 如何扩展一个 Provider

Provider Adapter 只负责将公共搜索意图映射到供应商 API，再将响应转换为 `SearchResponse` / `SearchResult`。`BaseSearchTool.execute()` 统一处理参数校验、API key 获取、搜索路径选择、URL 去重和 MCP 输出组装，Provider 不需要重写这些逻辑。

## 1. 实现最小 Adapter

在 `providers/` 下新建模块并继承 `BaseSearchTool`。只支持普通网页搜索的最小实现如下：

```python
from __future__ import annotations

from typing import Any

import httpx
from common.core.exceptions import ServiceException

from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import BaseSearchTool, SearchResponse, SearchResult


class ExampleSearchTool(BaseSearchTool):
    tool_name = "example_search"
    provider_name = "example"

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
    ) -> SearchResponse:
        if not api_key:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                "Example API key is required.",
            )

        try:
            response = await self._http_client.post(
                "https://api.example.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "limit": max_results},
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except httpx.HTTPError as exc:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_UNAVAILABLE,
                "Example search request failed.",
            ) from exc

        return SearchResponse(
            results=[
                SearchResult(
                    title=item.get("title"),
                    url=item.get("url"),
                    published_date=item.get("published_at"),
                    evidences=[item["snippet"]] if item.get("snippet") else [],
                    metadata={},
                )
                for item in data.get("results", [])
            ],
        )
```

`search_web` 必须保留 `query`、`max_results`、`api_key` 这三个公共参数。`api_key` 由基座从 MCP 调用配置中注入，不会暴露给模型。Provider 应保留有用的标题、URL、发表时间、证据文本及模型判断来源所需的 metadata，不把供应商响应原样透传到公共结果。

## 2. 用方法签名声明真实能力

MCP Tool Schema 由当前 Provider 的方法签名动态裁剪，不需要、也不应增加 capability flag 或 Provider 名称分支。

- Provider 原生支持 focused extraction 时，在 `search_web` 中额外声明 `focus: str | None = None`。不支持时不要声明，也不要将 `focus` 拼接到 `query`。
- Provider 能自然实现 `day/week/month/year` 四档新鲜度时，在方法中声明 `recency: SearchRecency | None = None`，并映射到最接近的原生时间过滤。无法完整实现四档时不要声明。
- Provider 有独立学术搜索路径时，override `search_academic`。仅有调用 `search_web` 的 fallback 不算独立学术能力。
- 存在独立学术路径时，只有 `search_web` 和 `search_academic` 都显式声明 `recency`，Schema 才会暴露它；基座不生成按 `mode` 分支的条件 Schema。

例如，同时支持三项可选能力的签名应为：

```python
async def search_web(
    self,
    *,
    query: str,
    max_results: int,
    api_key: str | None,
    focus: str | None = None,
    recency: SearchRecency | None = None,
) -> SearchResponse:
    ...

async def search_academic(
    self,
    *,
    query: str,
    max_results: int,
    api_key: str | None,
    focus: str | None = None,
    recency: SearchRecency | None = None,
) -> SearchResponse:
    ...
```

此时 Schema 暴露 `query`、`mode`、`focus`、`recency`、`max_results`。最小 Adapter 没有额外参数且没有 override `search_academic`，因此只暴露 `query`、`max_results`。

## 3. 处理凭证、异常和平台托管

普通 Provider 保持 `requires_api_key = True`，在 Adapter 中校验传入的 `api_key`。将无效凭证、配额不可用映射为 `WEB_SEARCH_CREDENTIAL_INVALID`，将超时、网络错误和供应商服务异常映射为 `WEB_SEARCH_UNAVAILABLE`，其他请求或响应错误映射为 `WEB_SEARCH_FAILED`。不在 Adapter 中吞掉异常或返回伪造的空结果。

只有平台托管工具才设置 `requires_api_key = False`，并从 `app_settings.py` 读取服务端密钥；此时不得让请求侧 key 覆盖平台配置。新增托管配置时，同步更新 Nacos 开发/生产配置模板。

## 4. 导出并注册

1. 在 `providers/__init__.py` 导出新的 Tool 类并加入 `__all__`。
2. 在 Web Search 包的 `__init__.py` 中实例化 Tool，加入 `web_search_tools`；如果 Adapter 使用共享 `httpx.AsyncClient`，在构造时注入它。
3. 如果使用新 SDK，将它加入 `services/wisepen-mcp-service/pyproject.toml` 并同步 lockfile。

`provider_name` 用供应商的稳定小写标识，`tool_name` 使用唯一的 MCP 工具名。平台默认工具是例外：它保持 `tool_name = "default_web_search"` 和 `provider_name = None`，底层供应商的替换不改变这个公共身份。

## 5. 最小验证

新 Provider 至少应验证：

- 通过 FastMCP 注册后的 `inputSchema` 只包含真实支持的 `mode`、`focus`、`recency`，且不包含 `ctx` 和 `api_key`。
- 执行网页/学术路径时，Provider 收到的 kwargs 与目标方法签名一致，隐藏参数不会导致缺参。
- 供应商响应正确映射为 `SearchResult`，包括空 snippet、缺失日期、无 URL 学术结果和超过 `max_results` 等边界。
- 凭证、限流、超时、非法 JSON 或响应结构变化会转换为预期的 `McpErrorCode`。
