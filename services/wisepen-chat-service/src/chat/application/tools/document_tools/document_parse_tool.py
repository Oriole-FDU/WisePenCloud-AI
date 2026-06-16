from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from chat.application.tools.common.tool_run_file_store import ToolRunFileStore
from chat.application.tools.common.tool_run_file_store.errors import (
    InvalidToolFileRefError,
    ToolFileNotFoundError,
    ToolFileUnreadableError,
)
from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.tool_return import (
    SuggestedAction,
    SuggestedActionPriority,
    ToolReturn,
)
from chat.application.tools.document_tools.document_parse.models import DocumentParseRequest
from chat.application.tools.document_tools.document_parse.service import DocumentParseService

MAX_DOCUMENT_PARSE_FILE_REFS = 8
DOCUMENT_PARSE_CONCURRENCY = 3


@dataclass(frozen=True, slots=True)
class DocumentParseToolItem:
    file_ref: str  # 调用方传入的 tfile_* 引用
    status: str  # success 或 failed
    file_name: str | None = None  # 解析出的展示文件名
    content_ref: int | None = None  # 对应 ToolReturn.cacheable_texts 的索引
    reason: str | None = None  # 单项失败原因，供模型判断下一步

class DocumentParseTool:
    """批量解析 tfile_* 文档引用的工具入口。"""

    __slots__ = ("_definition", "_file_store", "_parse_service")

    def __init__(
        self,
        *,
        file_store: ToolRunFileStore,
        parse_service: DocumentParseService,
    ) -> None:
        self._file_store = file_store
        self._parse_service = parse_service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="document_parse",
                description=(
                    "Parse temporary document files referenced by file_refs into Markdown.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when previous tools returned tfile_* references (e.g. from web_fetch, web_crawl, or uploads) and you need their textual content.\n"
                    "  - SHOULD trigger when the user asks to read, summarize, or answer questions about an attached document.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need to fetch URLs — use web_fetch or web_crawl instead.\n"
                    "  - You already have content_ids from a previous parse — use tool_content_read or tool_content_batch_read instead.\n"
                    "  - The file_ref is not a tfile_* value — never invent references.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - file_refs MUST be 1~8 tfile_* values returned by previous tools in this session.\n"
                    "  - Pass all selected file_refs in one array; the tool parses files concurrently.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns one item per file_ref with status success or failed.\n"
                    "  - Each successfully parsed file produces a cacheable content unit; failed files return a reason code.\n"
                    "  - Use the suggested tool_content_read action to locate answer-relevant windows in the parsed Markdown."
                ),
                parameters_schema=ToolParametersSchema(
                    {
                        "type": "object",
                        "properties": {
                            "file_refs": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "minItems": 1,
                                "maxItems": MAX_DOCUMENT_PARSE_FILE_REFS,
                                "description": (
                                    "Required. One to eight tfile_* references produced by previous tools. "
                                    "Pass all files for the same task in one call."
                                ),
                            },
                        },
                        "required": ["file_refs"],
                        "additionalProperties": False,
                    }
                ),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("user_id", "session_id"),
                timeout_seconds=120.0,
                cache_chunked=True,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        """返回工具元定义。"""
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        """批量解析文件引用，单项失败不影响其它文件。"""
        user_id = str(context["user_id"])
        session_id = str(context["session_id"])
        file_refs = tuple(str(value) for value in kwargs["file_refs"])

        semaphore = asyncio.Semaphore(DOCUMENT_PARSE_CONCURRENCY)
        item_results = await asyncio.gather(
            *[
                self._parse_one(
                    semaphore=semaphore,
                    user_id=user_id,
                    session_id=session_id,
                    file_ref=file_ref,
                )
                for file_ref in file_refs
            ],
            return_exceptions=False,
        )

        cacheable_texts: list[str] = []
        items: list[DocumentParseToolItem] = []
        for item, markdown in item_results:
            if markdown:
                item = DocumentParseToolItem(
                    file_ref=item.file_ref,
                    status=item.status,
                    file_name=item.file_name,
                    content_ref=len(cacheable_texts),
                )
                cacheable_texts.append(markdown)
            items.append(item)

        return ToolReturn(
            tag="document_parse_result",
            visible_result={
                "items": tuple(items),
                "suggested_action": SuggestedAction(
                    tool_name="tool_content_read",
                    mode="ranked_expand",
                    reason="Search the parsed Markdown content for answer-relevant windows.",
                    priority=SuggestedActionPriority.HIGH,
                ),
            },
            cacheable_texts=tuple(cacheable_texts),
        )

    async def _parse_one(
        self,
        *,
        semaphore: asyncio.Semaphore,
        user_id: str,
        session_id: str,
        file_ref: str,
    ) -> tuple[DocumentParseToolItem, str | None]:
        """解析单个文件引用；异常转换为单项失败。"""
        async with semaphore:
            try:
                resolved = await self._file_store.resolve_ref(
                    user_id=user_id,
                    session_id=session_id,
                    ref_id=file_ref,
                )
                result = await self._parse_service.parse(
                    DocumentParseRequest(
                        file_path=resolved.path,
                        original_filename=resolved.filename,
                        mime_type=resolved.content_type,
                    )
                )
                markdown = result.markdown.strip()
                return (
                    DocumentParseToolItem(
                        file_ref=file_ref,
                        status="success",
                        file_name=resolved.filename,
                    ),
                    markdown or None,
                )
            except Exception as e:
                return (
                    DocumentParseToolItem(
                        file_ref=file_ref,
                        status="failed",
                        reason=_failure_reason(e),
                    ),
                    None,
                )


def _failure_reason(error: Exception) -> str:
    if isinstance(error, InvalidToolFileRefError):
        return "invalid_file_ref"
    if isinstance(error, ToolFileNotFoundError):
        return "file_ref_unavailable"
    if isinstance(error, ToolFileUnreadableError):
        return "file_unreadable"
    return "parse_failed"
