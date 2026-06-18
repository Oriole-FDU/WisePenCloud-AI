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
from chat.application.tools.web_tools.hydrators import PaperHydrator


class PaperHydrateTool:
    """显式论文元数据补全工具。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: PaperHydrator) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="paper_hydrate",
                description=(
                    "Hydrate a paper candidate into structured metadata using OpenAlex only.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger only when you already have a concrete paper signal such as a DOI, OpenAlex id, or a specific paper title.\n"
                    "  - SHOULD trigger when the user needs finer paper metadata such as authors, venue, abstract, cited_by_count, open access, or landing url.\n"
                    "  - SHOULD trigger after search when a result is clearly a paper and structured metadata would materially improve the next step.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You only have a vague research topic or keyword query — use web_search instead.\n"
                    "  - The user needs the full paper content or PDF text — use web_fetch or document_parse instead.\n"
                    "  - The candidate is not clearly a paper.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - Prefer openalex_id when available, otherwise doi, otherwise title.\n"
                    "  - candidate_title is only a fallback when you do not have a dedicated title field.\n"
                    "  - Provide only fields you actually know; do not invent ids or metadata.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns structured paper metadata plus a hydration status: hydrated, partial, not_found, or failed.\n"
                    "  - partial means title search produced a low-confidence or ambiguous match.\n"
                    "  - This tool does not fetch PDFs, does not parse documents, and does not read page content.\n"
                ),
                parameters_schema=ToolParametersSchema(
                    {
                        "type": "object",
                        "properties": {
                            "openalex_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Preferred when known. An OpenAlex work id such as W1234567890 or https://openalex.org/W1234567890.",
                            },
                            "doi": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Use when the paper DOI is known. Accepts bare DOI, doi:..., or https://doi.org/... form.",
                            },
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Use when the paper title is known and exact enough for OpenAlex title search.",
                            },
                            "candidate_title": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Optional backup title from an existing candidate when no explicit paper title field is available.",
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
                timeout_seconds=tool_settings.PAPER_HYDRATE_TOOL_TIMEOUT_SECONDS,
                cache_chunked=False,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any):
        openalex_id = kwargs.get("openalex_id")
        doi = kwargs.get("doi")
        title = kwargs.get("title")
        candidate_title = kwargs.get("candidate_title")

        # 条件参数约束无法用当前 schema/preflight 表达，必须在工具门面保留。
        if not openalex_id and not doi and not title and not candidate_title:
            raise ToolExecutionError(
                reason="missing_paper_locator",
                detail_reason="At least one of openalex_id, doi, title, or candidate_title is required.",
                retryable=False,
            )

        try:
            return await self._service.hydrate(
                openalex_id=openalex_id,
                doi=doi,
                title=title,
                candidate_title=candidate_title,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="paper_hydrate_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
