from io import BytesIO
from typing import Dict, Any

from common.logger import log_error, log_event
from chat.application.tools.read_attachment_tool import BaseReadAttachmentTool


class ReadWordAttachmentTool(BaseReadAttachmentTool):
    """读取 Word 附件（.docx / .doc）"""

    @property
    def name(self) -> str:
        return "read_word_attachment"

    @property
    def description(self) -> str:
        return (
            "Read the text content of a Microsoft Word attachment file (.docx or .doc). "
            "Call with the object_key of the Word attachment you want to read."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "object_key": {
                    "type": "string",
                    "description": "The OSS object key of the Word attachment to read.",
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
            from docx import Document

            doc = Document(BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            text = "\n".join(paragraphs)
        except Exception:
            # 旧格式 .doc 回退 → OLE2 提取
            text = self._extract_ole2_text(content, ["WordDocument"])
            if not text:
                log_error("Word解析失败", None, object_key=object_key)
                return "[Tool Error] Failed to parse Word document (not a valid .docx or .doc file)."

        text = self._truncate(text)
        log_event("Word附件读取成功", object_key=object_key, content_length=len(text))
        return text
