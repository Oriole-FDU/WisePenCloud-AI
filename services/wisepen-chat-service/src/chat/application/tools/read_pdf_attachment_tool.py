from io import BytesIO
from typing import Dict, Any

from common.logger import log_error, log_event
from chat.application.tools.read_attachment_tool import BaseReadAttachmentTool


class ReadPdfAttachmentTool(BaseReadAttachmentTool):
    """读取 PDF 附件（使用 pypdf 提取文本）"""

    @property
    def name(self) -> str:
        return "read_pdf_attachment"

    @property
    def description(self) -> str:
        return (
            "Read the text content of a PDF attachment file (.pdf). "
            "Call with the object_key of the PDF attachment you want to read."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "object_key": {
                    "type": "string",
                    "description": "The OSS object key of the PDF attachment to read.",
                },
            },
            "required": ["object_key"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        object_key = self._resolve_object_key(kwargs)
        if object_key is None:
            return "[Tool Error] Missing required argument: object_key."

        content, error = await self._validate_and_download(context, object_key)
        if error:
            return error

        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            pages: list[str] = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    pages.append(page_text)
            text = "\n".join(pages)
        except Exception as e:
            log_error("PDF解析失败", e, object_key=object_key)
            return f"[Tool Error] Failed to parse PDF: {e}"

        text = self._truncate(text)
        log_event("PDF附件读取成功", object_key=object_key, content_length=len(text))
        return text
