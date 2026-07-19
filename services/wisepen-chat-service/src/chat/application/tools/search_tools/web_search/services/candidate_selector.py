from __future__ import annotations

import json
from typing import TYPE_CHECKING

from chat.application.utils.xml_markup import xml_attr, xml_cdata
from common.logger import info, warn

if TYPE_CHECKING:
    from chat.application.utils.llm_clients.query import QueryClient

    from .pipeline import WebSearchCandidate


_SYSTEM_PROMPT = """
# 角色

你是搜索候选选择器。

# 任务

根据 `search_query`，从候选结果中选择相关性和证据价值最高的候选，并按推荐优先级排序。

# 输入

输入包含若干 `<candidate>` 和最后一个 `<search_query>`：

- `candidate` 可能包含标题、URL、摘要和高亮片段。
- `id` 是形如 `[1]` 的唯一候选编号。

# 选择规则

- `selected_ids` 只能包含输入中存在的原始 `candidate id`。
- 最多返回 5 个高质量候选，允许返回为空，宁缺毋滥。
- 不得重复候选编号。
- 保留 `[1]` 形式的原始编号。

# 输出格式

仅输出严格 JSON 对象：

```json
{"selected_ids":["[1]","[2]"]}
```
""".strip()

_MAX_SELECTED_CANDIDATES = 5


async def select_recommended_ids(
    *,
    search_query: str,
    candidates: tuple[WebSearchCandidate, ...],
    max_recommended_candidates: int,
    fallback_candidates_count: int,
) -> tuple[str, ...]:
    if not candidates:
        return ()

    selected = await _select_candidate_ids(
        search_query=search_query,
        candidates_xml=_candidates_xml(candidates),
    )

    valid_ids = {candidate.candidate_id for candidate in candidates}
    selected = tuple(
        candidate_id for candidate_id in selected if candidate_id in valid_ids
    )[:max_recommended_candidates]

    if selected:
        return selected

    return tuple(
        candidate.candidate_id for candidate in candidates[:fallback_candidates_count]
    )


async def _select_candidate_ids(
    *,
    search_query: str,
    candidates_xml: str,
    client: QueryClient | None = None,
) -> list[str]:
    try:
        if client is None:
            from chat.application.utils.llm_clients import build_query_client
            from chat.core.config.app_settings import settings

            client = build_query_client(
                model=settings.QUERY_MODEL,
            )

        prompt = "\n".join(
            (
                candidates_xml,
                "",
                "<search_query>",
                xml_cdata(search_query.strip()),
                "</search_query>",
            )
        )

        result = await client.aquery(
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=1024,
        )
    except Exception as exc:
        warn(
            "search candidate selection skipped.",
            search_query=search_query.strip()[:80],
            reason=exc.__class__.__name__,
        )
        return []

    info(
        "selector.select_candidate_ids",
        search_query=search_query.strip()[:80],
        raw_response=result.content,
    )

    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError:
        return []

    raw_ids = payload.get("selected_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list):
        return []

    selected: list[str] = []
    seen: set[str] = set()

    for value in raw_ids:
        candidate_id = value.strip() if isinstance(value, str) else ""
        if not candidate_id or candidate_id in seen:
            continue

        selected.append(candidate_id)
        seen.add(candidate_id)

        if len(selected) == _MAX_SELECTED_CANDIDATES:
            break

    return selected


def _candidates_xml(
    candidates: tuple[WebSearchCandidate, ...],
) -> str:
    blocks: list[str] = []

    for candidate in candidates:
        lines = [
            f'<candidate id="{xml_attr(candidate.candidate_id)}">',
            f"  <title>{xml_cdata(candidate.title)}</title>",
            f"  <url>{xml_cdata(candidate.url)}</url>",
        ]

        if candidate.overview:
            lines.append(f"  <overview>{xml_cdata(candidate.overview)}</overview>")

        lines.extend(
            f"  <highlight>{xml_cdata(highlight)}</highlight>"
            for highlight in candidate.highlights
        )

        lines.append("</candidate>")
        blocks.append("\n".join(lines))

    return "\n".join(blocks)
