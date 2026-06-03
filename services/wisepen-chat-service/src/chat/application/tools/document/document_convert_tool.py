from pathlib import Path
from typing import Any, Dict, Optional

from chat.application.tools.document.formatting import format_generated_document_result
from chat.application.tools.document.services.document_convert import DocumentConvertService
from chat.application.tools.document.services.document_convert.errors import DocumentConvertError
from chat.application.tools.document.services.document_export.enums import ExportFormat
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

_TOOL_DESCRIPTION = (
    "Converts a server-side temporary document file_ref or a generated document "
    "download_ref into a generated downloadable file. Use this when the user wants "
    "an already-ingested or already-generated document converted into markdown, "
    "html, pdf, docx, or txt.\n\n"
    "The input MUST be either file_ref or download_ref, not a content_id.\n"
    "This tool does not accept raw content. Use document_export for direct content "
    "or cached ToolContent content_ref.\n"
    "The conversion service first converts the source file into Markdown, then uses "
    "the document export service to generate the requested output format.\n"
    "reference_docx_file_ref is only valid when target_format is docx."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "file_ref": {
            "type": "string",
            "minLength": 1,
            "description": (
                "file_ref value for a server-side temporary document, or a "
                "download_ref returned by document_export/document_convert. "
                "Do not pass cnt_* content_id values."
            ),
        },
        "download_ref": {
            "type": "string",
            "minLength": 1,
            "description": (
                "download_ref returned by document_export/document_convert. "
                "Use this to convert a previously generated downloadable document."
            ),
        },
        "target_format": {
            "type": "string",
            "enum": ["markdown", "html", "pdf", "docx", "txt"],
            "description": "Output file format.",
        },
        "file_name": {
            "type": "string",
            "minLength": 1,
            "description": "Optional output file name. Name only; not a path.",
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "description": "Optional document title for generated outputs.",
        },
        "reference_docx_file_ref": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Optional server-side temporary .docx file_ref used as a DOCX "
                "style reference. Only valid when target_format is docx."
            ),
        },
    },
    "required": ["target_format"],
    "additionalProperties": False,
}


class DocumentConvertTool(BaseTool):
    def __init__(self, *, convert_service: DocumentConvertService) -> None:
        self.convert_service = convert_service

    @property
    def name(self) -> str:
        return "document_convert"

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

        file_ref = kwargs.get("file_ref")
        download_ref: Optional[str] = kwargs.get("download_ref")
        if file_ref is not None and download_ref is not None:
            return "[Tool Error] Provide only one of file_ref or download_ref."
        if file_ref is None:
            file_ref = download_ref
        if file_ref is None:
            return "[Tool Error] Missing file_ref or download_ref."

        if file_ref.startswith("cnt_"):
            return (
                "[Tool Error] Invalid source reference: content_id values "
                "must be passed to tool_content_read, evidence_rank, or document_export."
            )
        if file_ref.startswith(("http://", "https://")):
            return (
                "[Tool Error] Invalid source reference: URLs must be passed "
                "to web_fetch, not document_convert."
            )

        target_format = ExportFormat(kwargs["target_format"])

        file_name: Optional[str] = kwargs.get("file_name")
        title: Optional[str] = kwargs.get("title")
        reference_docx_file_ref: Optional[str] = kwargs.get("reference_docx_file_ref")

        if reference_docx_file_ref is not None and target_format != ExportFormat.DOCX:
            return "[Tool Error] reference_docx_file_ref is only supported for docx export."

        if file_name is not None:
            output_suffix = Path(file_name).suffix.lower()
            if output_suffix and output_suffix != target_format.extension:
                return "[Tool Error] output file_name suffix conflicts with target_format."

        try:
            generated = await self.convert_service.convert_document(
                user_id=user_id,
                session_id=session_id,
                file_ref=file_ref,
                target_format=target_format,
                file_name=file_name,
                title=title,
                reference_docx_file_ref=reference_docx_file_ref,
            )

            return format_generated_document_result(
                user_id=user_id,
                session_id=session_id,
                generated=generated,
            )

        except DocumentConvertError as e:
            return f"[Tool Error] {e}"
        except Exception as e:
            log_fail(
                "document_convert",
                e,
                user_id=user_id,
                session_id=session_id,
                target_format=target_format.value,
            )
            return "[Tool Error] Unexpected error while converting document."
