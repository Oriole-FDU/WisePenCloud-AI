import json
import re
from typing import Any, Dict, List, Sequence

from chat.application.tool_scope import ToolScope
from chat.domain.interfaces.tool import BaseTool, ToolExposure

_SELECT_PREFIX_RE = re.compile(r"^(?:select|namespace):(.+)$", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-zA-Z0-9_\-\u4e00-\u9fff]+")

_TOOL_DESCRIPTION = (
    "Search and expose deferred tools for this turn. Most business tools are not visible "
    "in the first LLM step to reduce tool-schema context and to let matched Skills load "
    "before generic tools compete with them. Prefer exposing a tool namespace when the "
    "task needs a family of capabilities: use query='namespace:web', 'namespace:document', "
    "'namespace:math_solver', etc. query='select:<tool_name>' also works for exact tool "
    "names. Keyword queries such as 'web search' or 'document parse' return the best "
    "matching namespace/tool family. After this tool returns matches, those tools become "
    "callable in the next reasoning step."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Tool search query. Use 'namespace:<name>' to expose a tool family, "
                "or 'select:<tool_or_namespace>' with comma-separated values."
            ),
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "default": 5,
            "description": "Maximum number of matching tools to expose.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class ToolSearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "tool_search"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    @property
    def exposure(self) -> ToolExposure:
        return ToolExposure.BOOTSTRAP

    @property
    def search_hint(self) -> str:
        return "discover expose deferred hidden tools schema tool search"

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        tool_scope = context.get("tool_scope")
        if not isinstance(tool_scope, ToolScope):
            return "[Tool Error] Missing tool_scope in execution context."

        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return "[Tool Error] query must be a non-empty string."
        query = query.strip()

        max_results = kwargs.get("max_results", 5)
        if not isinstance(max_results, int):
            return "[Tool Error] max_results must be an integer."
        max_results = min(10, max(1, max_results))

        deferred_tools = tool_scope.list_deferred_tools()
        if not deferred_tools:
            return _format_result({
                "query": query,
                "matches": [],
                "exposed": [],
                "exposed_namespaces": {},
                "total_deferred_tools": 0,
            }, note="No deferred tools remain hidden in this turn.")

        matches: List[BaseTool] | None = None
        select_match = _SELECT_PREFIX_RE.match(query)
        if select_match is not None:
            requested = [
                item.strip().lower()
                for item in select_match.group(1).split(",")
                if item.strip()
            ]
            by_name = {tool.name.lower(): tool for tool in deferred_tools}
            selected_by_name: Dict[str, BaseTool] = {}
            for value in requested:
                if value in by_name:
                    selected_by_name[by_name[value].name] = by_name[value]
                else:
                    for tool in deferred_tools:
                        if value in {namespace.lower() for namespace in tool.namespaces}:
                            selected_by_name[tool.name] = tool
            matches = list(selected_by_name.values())

        if matches is None:
            matches = _search_by_namespace_keywords(query, deferred_tools, max_results)

        exposed = tool_scope.expose_deferred_tools([tool.name for tool in matches])
        exposed_set = set(exposed)
        exposed_namespaces = {
            namespace: tool.namespace_description(namespace)
            for tool in matches
            for namespace in tool.namespaces
            if tool.name in exposed_set
        }

        return _format_result({
            "query": query,
            "matches": [_format_tool_match(tool) for tool in matches],
            "exposed": exposed,
            "exposed_namespaces": exposed_namespaces,
            "total_deferred_tools": len(deferred_tools),
        }, note=_skill_first_note(context))


def _search_by_namespace_keywords(
    query: str,
    tools: Sequence[BaseTool],
    max_results: int,
) -> List[BaseTool]:
    terms = _tokenize(query)
    if not terms:
        return []

    grouped: Dict[str, List[BaseTool]] = {}
    for tool in tools:
        for namespace in tool.namespaces:
            grouped.setdefault(namespace, []).append(tool)

    namespace_scores: Dict[str, int] = {}
    for namespace, namespace_tools in grouped.items():
        sample = namespace_tools[0]
        haystack = " ".join(
            [
                namespace,
                sample.namespace_description(namespace),
                *[_tool_search_text(tool) for tool in namespace_tools],
            ]
        ).lower()
        name_tokens = set(_tokenize(namespace))
        score = 0
        for term in terms:
            if term in name_tokens:
                score += 14
            elif term in namespace.lower():
                score += 10
            elif term in haystack:
                score += 2
        if score > 0:
            namespace_scores[namespace] = score

    if namespace_scores:
        selected_namespaces = [
            namespace
            for namespace, _score in sorted(
                namespace_scores.items(),
                key=lambda item: (-item[1], item[0]),
            )[:max_results]
        ]
        return [
            tool
            for namespace in selected_namespaces
            for tool in grouped[namespace]
        ]

    scored: List[tuple[BaseTool, int]] = []
    for tool in tools:
        haystack = _tool_search_text(tool)
        name_tokens = set(_tokenize(tool.name))
        score = 0
        for term in terms:
            if term in name_tokens:
                score += 10
            elif term in tool.name.lower():
                score += 6
            elif term in haystack:
                score += 2
        if score > 0:
            scored.append((tool, score))

    return [
        tool
        for tool, _score in sorted(scored, key=lambda item: (-item[1], item[0].name))[
            :max_results
        ]
    ]


def _tokenize(text: str) -> List[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _tool_search_text(tool: BaseTool) -> str:
    return " ".join(
        part
        for part in [
            tool.name,
            " ".join(tool.namespaces),
            tool.search_hint,
            tool.description,
            json.dumps(tool.parameters_schema, ensure_ascii=False),
        ]
        if part
    ).lower()


def _format_tool_match(tool: BaseTool) -> str:
    if not tool.namespaces:
        return tool.name
    return f"{','.join(tool.namespaces)}.{tool.name}"


def _skill_first_note(context: Dict[str, Any]) -> str:
    allowed_skill_ids = context.get("allowed_skill_ids") or []
    if allowed_skill_ids:
        return (
            "Skill-first policy: relevant skills were matched for this turn. "
            "Load and follow the Skill instructions before using newly exposed generic tools, "
            "unless the Skill is clearly irrelevant or asks for these tools."
        )
    return ""


def _format_result(payload: Dict[str, Any], *, note: str = "") -> str:
    lines = [
        "[Tool Result] tool_search",
        json.dumps(payload, ensure_ascii=False, indent=2),
    ]
    if note:
        lines.extend(["", note])
    if payload["exposed"]:
        lines.extend([
            "",
            "The exposed tools are callable in the next reasoning step.",
        ])
    else:
        lines.extend(["", "No matching deferred tools were exposed."])
    return "\n".join(lines)
