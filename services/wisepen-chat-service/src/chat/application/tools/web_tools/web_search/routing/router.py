from __future__ import annotations

import json
import re

from chat.application.utils.llm_clients import LiteLLMQueryClient, query_client
from .endpoint_planner import SearchIntentRoute

ROUTE_CLASSIFICATION_SYSTEM_PROMPT = """\
<role>
  你是搜索路由分类器，负责将用户查询归入唯一的类别。
</role>

<categories>
  <category name="news">
    用户需要最新新闻、实时事件、近期进展、政策变动、市场行情，
    或带有明显当前时间敏感性的报道。
  </category>
  <category name="academic">
    用户需要论文、学术研究、学者观点、引用、期刊、会议记录、
    arXiv 预印本、专利或其他科研资料。
  </category>
  <category name="general">
    其他普通网页搜索：百科解释、教程、产品资料、地点信息，
    或不明显属于 news / academic 的查询。
  </category>
</categories>

<output_format>
  以 JSON 对象输出，格式严格为：{"route": "<category_name>"}
  category_name 只能是 news、academic、general 之一。
  不得包含其他字段、注释或 Markdown 代码块。
</output_format>"""


_ROUTE_MAP: dict[str, SearchIntentRoute] = {r.value: r for r in SearchIntentRoute}
_CLEANUP_RE = re.compile(r"[^\w]")


class WebSearchRouter:
    """使用小模型把搜索 query 分类到搜索路由。"""

    def __init__(
        self,
        *,
        client: LiteLLMQueryClient = query_client,
    ) -> None:
        self._client = client

    async def route(self, query: str) -> SearchIntentRoute:
        result = await self._client.aquery(
            prompt=f"<query>{query.strip()}</query>",
            system_prompt=ROUTE_CLASSIFICATION_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=32,  # {"route": "academic"} 约 10 token，留足余量
        )

        # 主路径：JSON 解析
        try:
            value = str(json.loads(result.content.strip()).get("route", "")).lower().strip()
        except (json.JSONDecodeError, AttributeError):
            # 降级：裸文本容错（去标点后精确匹配 → 子串匹配）
            value = _CLEANUP_RE.sub("", result.content.strip().lower())

        if route := _ROUTE_MAP.get(value):
            return route

        for key, route in _ROUTE_MAP.items():
            if key in value:
                return route

        return SearchIntentRoute.GENERAL