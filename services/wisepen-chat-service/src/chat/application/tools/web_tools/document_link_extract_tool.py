from __future__ import annotations

import asyncio
from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn
from chat.application.utils.url_security import UrlSecurityError

from .document_link_extract import (
    DocumentLinkExtractor,
    PdfParseMethod,
    UnsupportedDocumentTypeError,
)


MAX_URLS = 64

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_URLS,
            "description": (
                "One or more complete, publicly reachable direct URLs to PDF, DOCX, XLSX, "
                "or PPTX documents. Do not pass HTML pages, search queries, site names, "
                "or relative URLs."
            ),
        },
        "pdf_method": {
            "type": "string",
            "enum": [method.value for method in PdfParseMethod],
            "default": PdfParseMethod.EXACT.value,
            "description": (
                "PDF parsing method. exact uses the full document parser and is slower but "
                "handles complex or scanned PDFs; fast reads the native PDF text layer. This "
                "parameter is honored only when urls contains exactly one item. When urls "
                "contains multiple items, the tool silently uses fast PDF parsing for every "
                "PDF regardless of this value. This parameter has no effect for DOCX, XLSX, "
                "or PPTX documents."
            ),
        },
    },
    "required": ["urls"],
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
                    "Extract one or more known public binary document URLs into Markdown. Supports "
                    "PDF, DOCX, XLSX, and PPTX after validating the downloaded file bytes. Use exact "
                    "PDF parsing for a single complex, scanned, formula-heavy, or table-heavy PDF; "
                    "use fast PDF parsing when the native text layer is sufficient. If multiple "
                    "urls are provided, the tool runs them concurrently and silently forces fast "
                    "PDF parsing for every PDF, regardless of the pdf_method argument. Successful "
                    "documents are returned even when other URLs fail; failed items include exception "
                    "details in the visible result. Use web_fetch for HTML pages or convenient fast "
                    "consumption of direct PDF URLs. Other binary formats are rejected."
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
        urls = tuple(str(url).strip() for url in kwargs["urls"])
        pdf_method = PdfParseMethod(kwargs.get("pdf_method") or PdfParseMethod.EXACT)
        if len(urls) > 1:
            pdf_method = PdfParseMethod.FAST
        results = await asyncio.gather(
            *(
                self._extractor.extract(url, pdf_method=pdf_method)
                for url in urls
            ),
            return_exceptions=True,
        )

        items: list[dict[str, Any]] = []
        cacheable_texts: list[CacheableText] = []
        for url, result in zip(urls, results, strict=True):
            if isinstance(result, Exception):
                unsupported = isinstance(
                    result,
                    (UnsupportedDocumentTypeError, NotImplementedError, UrlSecurityError),
                )
                items.append(
                    {
                        "source_url": url,
                        "status": "failed",
                        "reason": (
                            "document_link_extract_unsupported"
                            if unsupported
                            else "document_link_extract_failed"
                        ),
                        "exception": {
                            "type": type(result).__name__,
                            "message": str(result),
                        },
                        "retryable": not unsupported,
                    }
                )
                continue

            items.append({"source_url": url, "status": "success"})
            cacheable_texts.append(
                CacheableText(
                    text=result,
                    is_md=True,
                    metadata={"source_url": url},
                )
            )

        return ToolReturn(
            visible_result={
                "items": tuple(items),
            },
            cacheable_texts=tuple(cacheable_texts),
        )
