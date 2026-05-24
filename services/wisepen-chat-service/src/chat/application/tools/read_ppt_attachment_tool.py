from io import BytesIO
from typing import Dict, Any

from common.logger import log_error, log_event
from chat.application.tools.read_attachment_tool import BaseReadAttachmentTool


class ReadPptAttachmentTool(BaseReadAttachmentTool):
    """读取 PPT 附件（.pptx / .ppt）"""

    @property
    def name(self) -> str:
        return "read_ppt_attachment"

    @property
    def description(self) -> str:
        return (
            "Read the text content of a Microsoft PowerPoint attachment file (.pptx or .ppt). "
            "Extracts text from all slides including shapes and tables. "
            "Call with the object_key of the PowerPoint attachment you want to read."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "object_key": {
                    "type": "string",
                    "description": "The OSS object key of the PowerPoint attachment to read.",
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
            from pptx import Presentation

            prs = Presentation(BytesIO(content))
            parts: list[str] = []
            for slide_num, slide in enumerate(prs.slides, 1):
                slide_parts: list[str] = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for paragraph in shape.text_frame.paragraphs:
                            t = paragraph.text.strip()
                            if t:
                                slide_parts.append(t)
                    if shape.has_table:
                        for row in shape.table.rows:
                            row_text = "\t".join(
                                cell.text.strip() for cell in row.cells
                            )
                            if row_text.strip():
                                slide_parts.append(row_text)
                if slide_parts:
                    parts.append(f"--- Slide {slide_num} ---")
                    parts.extend(slide_parts)
            text = "\n".join(parts)
        except Exception:
            # 旧格式 .ppt 回退 → OLE2 提取
            text = self._extract_ole2_text(content, ["PowerPoint Document"])
            if not text:
                log_error("PPT解析失败", None, object_key=object_key)
                return "[Tool Error] Failed to parse PowerPoint (not a valid .pptx or .ppt file)."

        text = self._truncate(text)
        log_event("PPT附件读取成功", object_key=object_key, content_length=len(text))
        return text
