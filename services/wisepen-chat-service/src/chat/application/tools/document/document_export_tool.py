from typing import Any, Dict, Optional

from chat.application.tools.document.formatting import format_generated_document_result
from chat.application.tools.document.services.document_export.enums import ExportFormat, ExportSourceFormat
from chat.application.tools.document.services.document_export.errors import DocumentExportError
from chat.application.tools.document.services.document_export.models import GeneratedDocumentFile
from chat.application.tools.document.services.document_export.service import DocumentExportService
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

_TOOL_DESCRIPTION = (
    "Exports Markdown-like text or cached tool content into a generated file. "
    "Supports markdown, html, pdf, docx, and txt. "
    "Use this when the user asks to save or export known text/content into a "
    "downloadable file.\n\n"
    "This tool does not parse binary files.\n"
    "This tool does not fetch URLs.\n"
    "For uploaded binary documents, use document_parse first and then pass known "
    "text or content_ref here.\n"
    "source_format controls whether input is interpreted as Markdown or plain text."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "target_format": {
            "type": "string",
            "enum": ["markdown", "html", "pdf", "docx", "txt"],
            "description": "Output file format.",
        },
        "source_format": {
            "type": "string",
            "enum": ["markdown", "plain_text"],
            "description": "Input interpretation: markdown or plain_text.",
        },
        "content": {
            "type": "string",
            "minLength": 1,
            "description": "Direct content to export.",
        },
        "content_ref": {
            "type": "string",
            "minLength": 1,
            "description": (
                "cnt_* cached ToolContent identifier. Do not pass file_ref values."
            ),
        },
        "file_name": {
            "type": "string",
            "minLength": 1,
            "description": "Optional output file name. Name only; not a path.",
        },
    },
    "required": ["target_format"],
    "additionalProperties": False,
}


class DocumentExportTool(BaseTool):

    def __init__(self, *, export_service: DocumentExportService, content_store):
        self.export_service = export_service
        self.content_store = content_store

    @property
    def name(self) -> str:
        return "document_export"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:

        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."
        user_id: Optional[str] = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."


        target_format = ExportFormat(kwargs["target_format"])
        source_format = ExportSourceFormat(kwargs.get("source_format", "markdown"))
        content_ref: Optional[str] = kwargs.get("content_ref")
        content: Optional[str] = kwargs.get("content")

        if content is None and content_ref is None:
            return "[Tool Error] content or content_ref not provided."

        if content_ref and content:
            return "[Tool Error] content and content_ref cannot both be provided."

        file_name: Optional[str] = kwargs.get("file_name")

        try:
            # 如果是内容引用，则从工具内容缓存读取
            if content_ref:
                content_ref = self.content_store.resolve_canonical_content_id(
                    session_id=session_id,
                    content_id=content_ref,
                )
                stored = self.content_store.get(
                    session_id=session_id,
                    content_id=content_ref,
                )
                if stored is None:
                    return (
                        "[Tool Error] Cached content not found, expired, or "
                        "inaccessible."
                    )
                content = stored.text
                source_format = ExportSourceFormat.MARKDOWN if stored.content_type == "text/markdown" else ExportSourceFormat.PLAIN_TEXT


            generated: GeneratedDocumentFile = await self.export_service.export_document(
                user_id=user_id,
                session_id=session_id,
                content=content,  # type: ignore
                target_format=target_format,
                source_format=source_format,
                file_name=file_name,
            )

            return format_generated_document_result(
                user_id=user_id,
                session_id=session_id,
                generated=generated,
            )

        except DocumentExportError as e:
            return f"[Tool Error] {e}"
        except Exception as e:
            log_fail(
                "document_export",
                e,
                session_id=session_id,
                target_format=target_format,
                content_ref=content_ref,
            )
            return "[Tool Error] Unexpected error while exporting document."
