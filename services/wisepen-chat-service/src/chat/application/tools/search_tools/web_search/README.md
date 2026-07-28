# Web Search 搜索源扩展指南

本文档说明如何为 `web_search` 增加一个新的外部搜索源。目标是让新增搜索源只承担协议适配，并保持现有的工具配置、错误语义、检索模式和重排流程不变。

## 模块边界

```text
<Provider>SearchTool
    -> BaseWebSearchTool
    -> SearchSourceFactory
    -> <Provider>Searcher
    -> SearchPipeline
    -> RankingPipeline
```

- `tools/`：定义对 LLM 暴露的工具名，并绑定 `SearchProviderName`。
- `services/providers/`：外部 HTTP 协议边界。请求参数、鉴权头和原始响应到 `SearchResponse` 的转换只放在这里。
- `services/sources.py`：根据工具配置创建对应的 provider，并声明其 API 基地址。
- `services/pipeline.py`：根据 `web` 或 `academic` 模式调用 provider，再对标准结果统一重排。不要在这里加入供应商分支。
- `container.py`：注册工具和 provider 所需的配置。

新 provider 是私有搜索源：每次工具调用从其 `api_key` 配置创建 searcher，缓存键也以 provider 区分。不要把用户 API key 写进 `AppSettings`、源码或日志。

## 扩展步骤

### 1. 先验证供应商契约

在写代码前，用真实 API 做一次最小搜索请求，确认：

1. 请求方法、路径、鉴权头和必填参数。
2. 普通搜索结果所在的响应路径，以及 title、URL、摘要字段。
3. 是否有真正的学术搜索能力，以及其请求参数和返回结构。
4. 搜索端点是否会隐式执行抓取；如果存在可选抓取参数，不要发送它们。

只接入搜索。Agent、Browser、Fetch、Crawl、Scrape 等能力应保留在各自工具域，不能借由搜索 provider 混入。

### 2. 声明 provider 名称

在 `services/models.py` 的 `SearchProviderName` 增加一个 `StrEnum` 成员。成员值是工具配置和缓存隔离使用的稳定标识，新增后不要随意改名。

### 3. 实现 provider

在 `services/providers/` 新建一个文件。通常由请求对象和 searcher 组成：

```python
@dataclass(frozen=True, slots=True)
class ExampleSearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10
    academic: bool = False

    def to_http_request(self) -> ProviderSearchHttpRequest:
        payload: dict[str, object] = {
            "query": self.query,
            "limit": self.max_results,
        }
        if self.academic:
            payload["category"] = "research"

        return ProviderSearchHttpRequest(
            method="POST",
            path="/search",
            json=payload,
        )


class ExampleSearcher(BaseProviderSearcher):
    provider = SearchProviderName.EXAMPLE
    request_class = ExampleSearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Example API key is required.")

        super().__init__(
            http_client=http_client,
            config=config,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    @staticmethod
    def map_response(
        data: dict[str, Any],
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=SearchProviderName.EXAMPLE,
            results=dedupe_results(
                (
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        snippet=item.get("snippet"),
                    )
                    for item in data["results"]
                ),
                limit=max_results,
            ),
        )
```

`BaseProviderSearcher` 已统一处理 HTTP 请求、状态码映射、网络异常和 JSON object 校验。provider 只需要描述外部协议，不能重复实现这些逻辑。

将返回值转换为 `SearchResult(title, url, snippet, highlights)`。只映射下游确实消费的字段；论文作者、发布时间、费用、供应商 request id 等字段没有消费者时不应添加到内部模型。

响应字段路径是第三方边界的一部分。使用直接索引，例如 `data["results"]`，使供应商 schema 变化明确失败；不要用层层 `.get()` 把错误响应伪装成空结果。

### 4. 按需覆写学术搜索

`ProviderSearcher.search_academic()` 默认回退到 `search_web()`。仅当供应商提供原生学术端点或明确的学术过滤条件时，才覆写该方法：

```python
async def search_academic(
    self,
    *,
    query: str,
    max_results: int,
) -> SearchResponse:
    return await self._execute_request(
        request=ExampleSearchRequest(
            query=query,
            max_results=max_results,
            academic=True,
        ),
        query=query,
        max_results=max_results,
    )
```

不要为了统一外观伪造论文元数据，也不要让学术模式调用供应商的 agent 或抓取接口。

### 5. 接入工厂、工具和容器

按以下顺序修改，保持每层只有一种职责：

1. 在 `services/providers/__init__.py` 导出新的 searcher。
2. 在 `services/sources.py` 的 `SearchSourceFactory` 中增加 base URL 字段、provider 到 searcher 的构造分支，以及 provider 到 base URL 的映射分支。
3. 在 `tools/provider_tools.py` 定义一个只绑定工具名和 provider 枚举的 `ExampleSearchTool`。
4. 在 `tools/__init__.py` 和模块根 `__init__.py` 导出该工具。
5. 在 `core/config/app_settings.py` 添加 `WEB_SEARCH_<PROVIDER>_BASE_URL`，默认值使用官方 API 地址。
6. 在 `container.py` 将 base URL 注入 `SearchSourceFactory`，创建工具单例，并把它加入 `tool_providers`。

新搜索源的密钥由 `BaseWebSearchTool` 的既有 `api_key` 工具配置接收，并受 `secret_keys` 保护，不需要新增配置 schema 或环境变量字段。

## 测试清单

为新 provider 增加 `httpx.MockTransport` 测试，至少验证：

1. 普通搜索的 HTTP 方法、路径、鉴权头和 query/body。
2. 原始响应正确映射为 `SearchResponse` 和 `SearchResult`。
3. 若供应商支持学术检索，学术模式发送了正确的原生过滤条件。
4. 请求中没有 `scrapeOptions`、fetch、agent 或其他非搜索字段。
5. 模块根的公开导出测试包含新工具。

推荐执行：

```powershell
uv run pytest src/chat/tests/web_tools/test_web_search_providers.py src/chat/tests/web_tools/test_web_search_exports.py
uv run ruff check src/chat/application/tools/search_tools/web_search src/chat/tests/web_tools
```

最后用真实 API 再做一次普通搜索；存在原生学术能力时，也做一次学术搜索。只记录脱敏后的状态、字段名和结果数量，不记录 API key 或完整敏感响应。

## 现有示例

- `services/providers/exa.py`：请求体中的 `category` 控制学术检索。
- `services/providers/tinyfish.py`：`GET` 请求、`X-API-Key` 鉴权和 `domain_type=research_paper`。
- `services/providers/firecrawl.py`：`POST /v2/search`、Bearer 鉴权和 `categories=["research"]`；只发送搜索字段，不发送 `scrapeOptions`。
