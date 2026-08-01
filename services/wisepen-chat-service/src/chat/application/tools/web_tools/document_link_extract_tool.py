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
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn
from chat.application.tools.utils.url import UrlSecurityError

from .document_link_extract import (
    DocumentLinkExtractor,
    PdfParseMethod,
    UnsupportedDocumentTypeError,
)


_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "minLength": 1,
            "description": (
                "One complete, publicly reachable direct URL to a PDF, DOCX, XLSX, or PPTX "
                "document. Do not pass an HTML page, search query, site name, or relative URL."
            ),
        },
        "pdf_method": {
            "type": "string",
            "enum": [method.value for method in PdfParseMethod],
            "default": PdfParseMethod.EXACT.value,
            "description": (
                "PDF parsing method. exact uses the full document parser and is slower but "
                "handles complex or scanned PDFs; fast reads the native PDF text layer. This "
                "parameter has no effect for DOCX, XLSX, or PPTX documents."
            ),
        },
    },
    "required": ["url"],
    "additionalProperties": False,
}


class DocumentLinkExtractTool:
    __slots__ = ("_definition", "_extractor")

    def __init__(self, *, extractor: DocumentLinkExtractor) -> None:
        self._extractor = extractor
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="document_link_extract",
                description=(
                    "Extract one known public binary document URL into Markdown. Supports PDF, "
                    "DOCX, XLSX, and PPTX after validating the downloaded file bytes. Use exact "
                    "PDF parsing for complex, scanned, formula-heavy, or table-heavy documents; "
                    "use fast PDF parsing when the native text layer is sufficient. Use web_fetch "
                    "for HTML pages or convenient fast consumption of direct PDF URLs. Other "
                    "binary formats are rejected."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=3600.0,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolReturn:
        del context, config
        url = str(kwargs["url"]).strip()
        try:
            markdown = await self._extractor.extract(
                url,
                pdf_method=PdfParseMethod(
                    kwargs.get("pdf_method") or PdfParseMethod.EXACT
                ),
            )
        except (UnsupportedDocumentTypeError, NotImplementedError, UrlSecurityError) as exc:
            raise ToolExecutionError(
                reason="document_link_extract_unsupported",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except Exception as exc:
            raise ToolExecutionError(
                reason="document_link_extract_failed",
                detail_reason=str(exc),
                retryable=True,
            ) from exc

        return ToolReturn(
            visible_result={"source_url": url},
            cacheable_texts=(
                CacheableText(
                    text=markdown,
                    is_md=True,
                    metadata={"source_url": url},
                ),
            ),
        )
