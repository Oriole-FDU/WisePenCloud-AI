from typing import Dict, Any

from common.logger import log_event
from chat.application.tools.read_attachment_tool import BaseReadAttachmentTool


class ReadTextAttachmentTool(BaseReadAttachmentTool):
    """读取纯文本/代码类附件（.txt .md .py .js .json .yaml .csv .html 等）"""

    @property
    def name(self) -> str:
        return "read_text_attachment"

    @property
    def description(self) -> str:
        return (
            "Read the content of a text or code attachment file. "
            "Supports: .txt .md .py .js .ts .json .xml .yaml .yml .csv .html .css "
            ".java .go .rs .c .cpp .h .sh .bat .sql .log .ini .cfg .toml .env. "
            "Call with the object_key of the attachment you want to read."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "object_key": {
                    "type": "string",
                    "description": "The OSS object key of the text/code attachment to read.",
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

        for encoding in ("utf-8", "gb18030", "utf-16", "big5"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            return "[Tool Error] Failed to decode text content with any supported encoding."

        text = self._truncate(text)
        log_event("文本附件读取成功", object_key=object_key, content_length=len(text))
        return text
