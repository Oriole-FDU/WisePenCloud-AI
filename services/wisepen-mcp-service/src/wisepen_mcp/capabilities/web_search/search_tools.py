from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any, ClassVar

from common.core.exceptions import ServiceException
from common.mcp import (
    OutputCacheSpecMeta,
    OutputCacheStrategy,
    ToolConfigSpecMeta,
    ToolMeta,
    ToolPolicyMeta,
    ToolRiskLevel,
    ToolUISpecMeta,
    register_tool_meta,
)
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field
from pydantic.fields import FieldInfo

from wisepen_mcp.capabilities.core.tool_metadata import get_tool_config_value
from wisepen_mcp.domain.error_codes import McpErrorCode

from .model import (
    WebSearchRequest,
    SearchMode,
    SearchRecency,
    SearchResponse,
    SearchResult,
    WebSearchToolResult,
)

TOOL_DESCRIPTION = (
    "### Purpose\n"
    "Search external resources for relevant evidence to support factual reasoning.\n\n"
    "### Rules\n"
    "1. Use concise, specific search terms targeting the information needed.\n"
    "2. Treat `results[].evidences` as the primary factual grounding. Use source title, "
    "URL, publication date, and metadata to assess relevance, authority, and freshness.\n"
    "3. Do not infer unsupported facts. If the evidence is weak, irrelevant, ambiguous, "
    "or insufficient, refine the search instead of stretching it.\n"
    "4. Treat any top-level `summary` only as a lead; factual claims should be supported "
    "by source evidence.\n"
    "5. When using search evidence in the final answer, cite the supporting source "
    "with a Markdown link: [concise source label](exact_source_url). Place the link "
    "immediately after the claim or the smallest group of related claims it supports. "
    "Do not collect all citations at the end of the answer.\n"
    "6. Write short, meaningful link labels using the source's name, a shortened title, "
    "or its role (e.g., 'official documentation' or 'original paper'). Never display "
    "a long raw URL as the link label, and avoid generic labels such as 'click here'. "
    "Preserve the source URL exactly as provided; do not invent or rewrite URLs.\n"
    "7. Cite selectively and accurately: one link may support several adjacent claims "
    "grounded in the same source. Use separate links when claims rely on different "
    "sources. Cite only sources whose evidence actually supports the nearby claim; "
    "do not add links merely to decorate the answer.\n"
)


_WEB_SEARCH_POLICY_META = ToolPolicyMeta(
    expose_by_default=True,
    risk_level=ToolRiskLevel.LOW,
    timeout_seconds=100.0,
    persist_output=True,
)
_WEB_SEARCH_OUTPUT_CACHE_SPEC = OutputCacheSpecMeta(
    strategy=OutputCacheStrategy.LONG_TEXT,
    cache_paths=("results.evidences.*", "summary"),
)

_WEB_SEARCH_REQUIRED_API_KEY_CONFIG = ToolConfigSpecMeta(
    config_schema={
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "title": "API Key",
                "description": "API key for the configured search provider.",
                "writeOnly": True,
            },
        },
        "additionalProperties": False,
    },
    required_keys=("api_key",),
    secret_keys=("api_key",),
)

_WEB_SEARCH_OPTIONAL_API_KEY_CONFIG = ToolConfigSpecMeta(
    config_schema={
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "title": "DeepSeek API Key",
                "description": (
                    "Optional. Your own DeepSeek API key; leave empty to use the "
                    "platform-managed search key."
                ),
                "writeOnly": True,
            },
        },
        "additionalProperties": False,
    },
    required_keys=(),
    secret_keys=("api_key",),
)


def _keyword_parameters(handler: Callable) -> set[str]:
    """取出一个可调用对象显式声明的关键字参数名"""
    return {
        name
        for name, parameter in inspect.signature(handler).parameters.items()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }


def _filter_search_kwargs(handler: Callable, **kwargs: Any) -> dict[str, Any]:
    """把通用 kwargs 裁剪成目标 handler 接收的参数"""
    parameters = _keyword_parameters(handler)
    return {
        name: value
        for name, value in kwargs.items()
        if name in parameters
    }


def _field_annotation(field: FieldInfo) -> Any:
    """把输入模型的字段还原成带 Field 元数据的注解，供合成函数签名使用"""
    metadata = [*field.metadata]
    # description 是 FieldInfo 的独立属性，必须单独重新包一个 Field 回来，否则工具 Schema 里所有参数说明会静默消失
    if field.description:
        metadata.append(Field(description=field.description))
    annotation = (
        Annotated[tuple([field.annotation, *metadata])]
        if metadata
        else field.annotation
    )
    return annotation


def _request_parameters(hidden: set[str]) -> list[inspect.Parameter]:
    """按输入模型合成工具参数，并剔除本 provider 不支持的那些"""
    parameters = [
        inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context)
    ]
    for name, field in WebSearchRequest.model_fields.items():
        if name in hidden:
            continue
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=_field_annotation(field),
                default=(
                    inspect.Parameter.empty
                    if field.is_required()
                    else field.get_default(call_default_factory=True)
                ),
            )
        )
    return parameters


class BaseSearchTool(ABC):
    tool_name: ClassVar[str]
    tool_meta: ClassVar[ToolMeta]
    provider_name: ClassVar[str | None] = None
    description: ClassVar[str] = TOOL_DESCRIPTION
    requires_api_key: ClassVar[bool] = True

    def _has_academic_search(self) -> bool:
        """判断 provider 是否实现了独立的学术搜索路径"""
        return type(self).search_academic is not BaseSearchTool.search_academic

    def register(self, mcp: FastMCP) -> None:
        hidden_parameters: set[str] = set()
        description = self.description

        # 获取 provider 的 search_web 签名
        web_parameters = _keyword_parameters(self.search_web)

        # 有独立学术路径才暴露 mode，否则隐藏
        if self._has_academic_search():
            description += (
                "\n### Academic Search\n"
                "Use `mode='academic'` when the task specifically requires papers, "
                "preprints, or scholarly publications rather than general web sources.\n"
            )
        else:
            hidden_parameters.add("mode")

        # 有 focus 参数才暴露 focus，否则隐藏
        if "focus" in web_parameters:
            description += (
                "\n### Focused Extraction\n"
                "Use `focus` for the specific fact, metric, comparison, or passage to "
                "prioritize within matched sources. Keep `query` optimized for retrieval "
                "and `focus` optimized for evidence extraction.\n"
            )
        else:
            hidden_parameters.add("focus")

        # 要求 web 和（若存在）学术两条路径都有 recency 才暴露 recency，否则隐藏
        if "recency" in web_parameters and (
            not self._has_academic_search()
            or "recency" in _keyword_parameters(self.search_academic)
        ):
            description += (
                "\n### Recency\n"
                "Use `recency` (day, week, month, year) as a soft freshness filter; "
                "omit it for unrestricted search. Exact boundaries follow the provider. "
                "Express precise date requirements in `query`.\n"
            )
        else:
            hidden_parameters.add("recency")

        for name in ["include_domains", "exclude_domains"]:
            if name not in web_parameters:
                hidden_parameters.add(name)
            elif self._has_academic_search() and name not in _keyword_parameters(self.search_academic):
                # 部分方法学术模式不支持上述参数，应特别说明
                description += (f"\n`{name}` is supported only with mode='web'; do not use it with mode='academic'.\n")

        @wraps(self.execute)
        async def execute(**kwargs: Any) -> WebSearchToolResult:
            ctx = kwargs.pop("ctx")
            return await self.execute(
                ctx=ctx,
                request=WebSearchRequest(**kwargs),
            )

        # 输入契约由 ProviderSearchRequest 声明，这里按 provider 能力裁剪后注回包装函数
        signature = inspect.Signature(
            _request_parameters(hidden_parameters),
            return_annotation=WebSearchToolResult,
        )
        # FastMCP 用 __signature__ 建参数模型，决定对外 JSON Schema 有哪些字段、哪些必填
        execute.__signature__ = signature
        # FastMCP 用它识别 Context
        execute.__annotations__ = {
            parameter.name: parameter.annotation
            for parameter in signature.parameters.values()
        }
        # 决定结构化输出 Schema，缺了工具就退化成纯文本返回
        execute.__annotations__["return"] = WebSearchToolResult

        mcp.tool(
            name=self.tool_name,
            description=description,
        )(execute)
        register_tool_meta(self.tool_meta)

    async def execute(
        self,
        *,
        ctx: Context,
        request: WebSearchRequest,
    ) -> WebSearchToolResult:
        query = request.query.strip()
        if not query:
            raise ServiceException(McpErrorCode.WEB_SEARCH_INVALID,"query must not be blank.")

        # 未实现独立学术路径时，输出 scope 也应反映实际执行的网页搜索
        mode = request.mode if self._has_academic_search() else SearchMode.WEB
        # focus 是自由文本，首尾空白对供应商无意义，空串视为未提供
        focus = request.focus.strip() if request.focus and request.focus.strip() else None

        api_key = get_tool_config_value(ctx, "api_key")
        api_key = (
            api_key.strip()
            if isinstance(api_key, str) and api_key.strip()
            else None
        )
        if self.requires_api_key and not api_key:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_CONFIG_MISSING,
                f"{self.tool_name} API key is not configured.",
            )

        # 用归一后的 mode 选路径
        handler = (
            self.search_academic
            if mode is SearchMode.ACADEMIC
            else self.search_web
        )
        domain_filters = {
            "include_domains": request.include_domains,
            "exclude_domains": request.exclude_domains,
        }
        handler_parameters = _keyword_parameters(handler)

        # 域名守卫
        for name, domains in domain_filters.items():
            # 不让通用 kwargs 裁剪静默放宽调用方明确要求的来源约束
            if domains and name not in handler_parameters:
                raise ServiceException(McpErrorCode.WEB_SEARCH_INVALID, f"{self.tool_name} does not support {name} with mode='{mode.value}'.")

        # 按目标 handler 的真实签名裁剪后再调用
        kwargs = _filter_search_kwargs(
            handler,
            query=query,
            focus=focus,
            recency=request.recency,
            max_results=request.max_results,
            api_key=api_key,
            **domain_filters,
        )
        response = await handler(**kwargs)

        # 按 URL 去重、再按条数截断
        seen_urls: set[str] = set()
        search_results: list[SearchResult] = []

        for result in response.results:
            # 无 URL 的论文结果不能以 None 互相去重，否则会丢失不同论文的标题与摘要证据
            if result.url and result.url in seen_urls:
                continue
            if result.url:
                seen_urls.add(result.url)

            search_results.append(result)
            if len(search_results) >= request.max_results:
                break

        # 空结果单独报错，让模型知道搜到了但没结果，与调用失败区分开
        if not search_results:
            raise ServiceException(McpErrorCode.WEB_SEARCH_EMPTY_RESULT, "The search provider returned no results.")

        return WebSearchToolResult(query=query, mode=mode, results=search_results, summary=response.summary)

    @abstractmethod
    async def search_web(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        """网页搜索，其签名是能力声明"""
        pass

    async def search_academic(self, *, query: str, max_results: int, api_key: str | None, focus: str | None = None, recency: SearchRecency | None = None) -> SearchResponse:
        """默认的「学术搜索」，其签名是能力声明，在没有独立学术接口的 provider 回退到网页搜索"""
        # 过滤参数以避免调用失败
        kwargs = _filter_search_kwargs(self.search_web, query=query, focus=focus, recency=recency, max_results=max_results, api_key=api_key)
        return await self.search_web(**kwargs)
