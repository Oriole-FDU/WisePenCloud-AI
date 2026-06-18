from __future__ import annotations

from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.tool_settings import tool_settings
from chat.application.tools.web_tools.hydrators import GitHubHydrator


class GitHubHydrateTool:
    """显式 GitHub 仓库元数据补全工具。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: GitHubHydrator) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="github_hydrate",
                description=(
                    "Hydrate a GitHub repository candidate into structured repository metadata using the GitHub API.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger only when you already have a concrete GitHub repository signal such as a repository URL or owner/repo.\n"
                    "  - SHOULD trigger when the user needs finer repository metadata such as topics, license, default branch, stars, forks, issues, or last update time.\n"
                    "  - SHOULD trigger after search when a result is clearly a GitHub repository and structured metadata would materially improve the next step.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You only have a general coding topic or package name with no clear repository target — use web_search instead.\n"
                    "  - The user needs README text, source code, or repository file contents.\n"
                    "  - The candidate is not clearly a GitHub repository.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - Prefer owner + repo when already known; otherwise pass a GitHub URL.\n"
                    "  - The URL may point to the repo root or to issue, pull, blob, tree, or release pages within the repo.\n"
                    "  - Provide only fields you actually know; do not invent owner/repo pairs.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns structured repository metadata plus a hydration status: hydrated, not_found, or failed.\n"
                    "  - This tool does not clone repositories, does not fetch the README, and does not read source files.\n"
                ),
                parameters_schema=ToolParametersSchema(
                    {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "minLength": 1,
                                "description": "A GitHub URL pointing at a repository or one of its issue, pull, blob, tree, or release pages.",
                            },
                            "owner": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Repository owner or organization. Use together with repo when you already know the canonical pair.",
                            },
                            "repo": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Repository name. Use together with owner when you already know the canonical pair.",
                            },
                        },
                        "additionalProperties": False,
                    }
                ),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                timeout_seconds=tool_settings.GITHUB_HYDRATE_TOOL_TIMEOUT_SECONDS,
                cache_chunked=False,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any):
        url = kwargs.get("url")
        owner = kwargs.get("owner")
        repo = kwargs.get("repo")

        # 条件参数约束无法用当前 schema/preflight 表达，必须在工具门面保留。
        if not url and not (owner and repo):
            raise ToolExecutionError(
                reason="missing_github_locator",
                detail_reason="Provide either url or both owner and repo.",
                retryable=False,
            )

        try:
            return self._service.hydrate(url=url, owner=owner, repo=repo)
        except Exception as exc:
            raise ToolExecutionError(
                reason="github_hydrate_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
