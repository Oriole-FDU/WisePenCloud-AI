# 平台默认 Web Search

`default_web_search` 使用平台托管的 DeepSeek Search，保留原工具名和注册方式。MCP 输入包含必填 `query`、可选 `max_results`（默认 10，范围 1–20）、`recency`、`include_domains` 和 `exclude_domains`，不暴露 `mode`、`focus` 或用户 API key。

在 MCP 服务的 Nacos 应用配置（`wisepen-mcp-service-dev.yaml` / `wisepen-mcp-service-prod.yaml`）中配置 DeepSeek 的 `WEB_SEARCH_API_KEY`。默认空值会在请求前返回配置缺失错误；请求侧 key 不会覆盖平台密钥。`WEB_SEARCH_DEEPSEEK_BASE_URL` 默认为 `https://api.deepseek.com`，请求路径为 `/v1/tools/search`。从 GLM 迁移时需替换托管密钥，并移除旧的 `WEB_SEARCH_GLM_BASE_URL`；如需自定义地址，改为配置新的 Base URL。仓库中没有这两个 Nacos 应用配置文件，上线时由部署流程更新。

请求固定使用 `need_content=false`，`count` 对应 `max_results`。每条网页优先取非空 `summary` 作为单条 `evidences`，缺失时使用 `snippet`；保留标题、链接、发表时间和 `rerank_score`、`authority`、非空 `site_name` 元数据。保留供应商排序，不按分数过滤，不重复输出摘要，不生成顶层 `summary`。即使供应商响应包含 `content`，也不会读取或输出；全文读取由平台 Web Fetch 能力负责。

`recency` 将 UTC 当前日期减去 1／7／30／365 天，分别对应 `day/week/month/year`，生成 `after:YYYY-MM-DD` 并追加到查询。这是按日期执行的软时间过滤；原查询中的 `site:`、`lang:`、`after:`、`before:` 等语法原样保留，由 DeepSeek 解释组合条件。最终发送的查询（包含域名和日期语法）最多 128 个字符，超限返回参数错误并提示缩短查询或减少域名，不再静默截取。公共结果的 `query` 仍保留去除首尾空白后的用户查询。

HTTP 400 映射为参数错误，401／402／403 映射为凭证或账户不可用，408／429／5xx 及网络超时映射为服务不可用，其余 HTTP 错误或非法响应映射为请求失败。不自动重试或回退；合法空数组沿用基座的无结果错误。

官方接口、搜索语法与计费说明见 [DeepSeek 搜索与抓取文档](https://api-docs.deepseek.com/zh-cn/guides/search/)。

## 接入验证记录

2026-10-17，通过实际 `PlatformSearchTool.execute()` 请求 `asyncio TaskGroup site:docs.python.org`，设置 `max_results=3`。请求发送到 `/v1/tools/search`，固定 `need_content=false`，返回 HTTP 200 和 3 条候选；上游三条 `content` 均为空，公共结果的摘要长度分别为 1,389／629／592 字符，评分与站点信息映射正常，顶层 `summary` 为 `null`。该记录验证适配器连通性与结果契约，不代表搜索质量基准或线上 Nacos 配置已完成迁移。

聚焦测试覆盖平台密钥优先级、四档 UTC 日期过滤、包含日期语法的 128 字符限制、摘要选择、全文隔离、错误映射、基座去重和 FastMCP Schema。运行：

```powershell
.\.venv\Scripts\python.exe -m pytest services/wisepen-mcp-service/tests/web_search/test_platform_default.py -q
```

# 域名约束

`include_domains` 表示仅从指定域名及其子域搜索，`exclude_domains` 表示排除指定域名及其子域。两者使用 `list[str]`；省略、`null` 或空列表均不添加对应条件。公共入口去除每个域名的首尾空白并转为小写，仅接收域名或子域名，例如 `python.org`、`docs.python.org`。URL、路径、端口、通配符、空字符串和搜索操作符会被拒绝，不查询 DNS，也不自动修正输入。国际化域名可使用 ASCII 的 Punycode 形式。

这些参数映射到供应商原生过滤，不是来源提权。供应商负责实际域名匹配；不在本地删选结果、补搜或放宽约束。不统一降低各家列表数量上限，超过原生限制时由供应商返回错误。

| 工具 | 网页搜索 | 学术搜索 | 实际请求 |
| --- | --- | --- | --- |
| `default_web_search` | 包含、排除，可组合 | 无独立路径 | `query` 追加 `site:a OR site:b -site:x`，再追加日期语法；不加括号 |
| `exa_search` | 包含、排除，可组合 | 包含、排除，可组合 | SDK `include_domains`／`exclude_domains` → JSON `includeDomains`／`excludeDomains` |
| `tavily_search` | 包含、排除，可组合 | 无独立路径 | JSON `include_domains`／`exclude_domains`，不发送 `include_domains_mode`，使用默认硬过滤 |
| `tinyfish_search` | 包含、排除，可组合 | 包含、排除，可组合 | GET 查询参数，列表以逗号连接 |
| `baidu_qianfan_search` | 仅包含 | 无独立路径 | JSON `search_filter.match.site`，原生上限 20 个；与日期 `range` 条件合并 |
| `firecrawl_search` | 包含或排除，两者互斥 | 不支持域名条件 | SDK 蛇形参数 → JSON `includeDomains`／`excludeDomains` |
| `anysearch_search` | 不暴露 | 不暴露 | 当前接口未提供已确认的网站域名过滤参数 |

公共参数由网页方法的显式签名决定是否暴露。存在独立学术路径时，工具描述会明确标注仅在 `mode='web'` 支持的域名参数。执行时若所选路径不支持非空域名条件，返回 `WEB_SEARCH_INVALID`，不静默丢弃；Firecrawl 同时包含和排除也在请求前返回该参数错误。用户在 `query` 中手写的语法原样保留，由供应商解释组合条件。

## 域名实验记录

2026-10-17，使用 `Python asyncio TaskGroup` 对各家做无过滤、包含 `python.org`、包含 `docs.python.org` 和排除根域的最小实验。Exa、Tavily、TinyFish、DeepSeek 与 Firecrawl 的已支持条件均得到符合约束的结果；根域样本可返回其子域。百度验证了包含条件及多个站点。Exa、TinyFish 另外以 `retrieval augmented generation` 验证学术路径的包含／排除，均生效。

Firecrawl 同时包含与排除返回 HTTP 400，当前学术 SDK 不接收域名参数。AnySearch 试传 `include_domains` 返回 HTTP 200，但仍出现多个列表外域名，故不声明该能力；其免费能力发现接口本次返回 502，`domain`／`sub-domain` 也不能等同于网站域名。

DeepSeek 的括号分组样本返回空结果，采用官方平铺语法。接入后通过真实 `PlatformSearchTool.execute()` 补测了 `include_domains=["python.org", "github.com"]`、`exclude_domains=["docs.python.org", "mail.python.org"]` 和 `recency="year"` 的组合：返回 5 条 GitHub 候选，包含与排除均符合要求，`need_content=false`，顶层 `summary=null`。以上仅验证参数行为，不代表各家搜索质量或所有域名匹配边界的全面保证。

对应官方协议：[DeepSeek](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Exa](https://exa.ai/docs/reference/search)、[Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[TinyFish](https://docs.tinyfish.ai/search-api/reference.md)、[百度千帆](https://ai.baidu.com/ai-doc/AppBuilder/pmaxd1hvy)、[Firecrawl 网页](https://docs.firecrawl.dev/api-reference/endpoint/search)、[Firecrawl 学术](https://docs.firecrawl.dev/api-reference/endpoint/research-search-papers)、[AnySearch](https://anysearch.com/docs/api-endpoints/v1-search)。

域名测试通过 MockTransport 保留真实 SDK 序列化，并检查各家模型可见描述和完整 Schema 中没有未声明参数的残留提示：

```powershell
.\.venv\Scripts\python.exe -m pytest services/wisepen-mcp-service/tests/web_search -q
```


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
- 域名参数分别由 `search_web` 是否声明 `include_domains`／`exclude_domains` 决定。允许学术路径缺少这两项能力，但必须保持真实签名；基座会在描述中说明网页限定，并在学术调用携带非空约束时明确报错。

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
