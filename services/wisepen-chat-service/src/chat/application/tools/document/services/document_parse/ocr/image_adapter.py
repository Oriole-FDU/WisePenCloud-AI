from pathlib import Path

from chat.application.tools.document.services.document_parse.ocr.errors import OcrProcessingError
from chat.application.tools.document.services.document_parse.ocr.processor import OcrProcessor


class OcrImageAdapter:
    """本地图片 OCR 适配器，封装 OcrProcessor 提供图片文字识别接口。"""

    def __init__(self, *, local_ocr_processor: OcrProcessor):
        """初始化 OcrImageAdapter，注入 OCR 处理器实例。"""
        self.local_ocr_processor = local_ocr_processor

    async def extract_text(self, image_path: Path) -> str:
        """对指定图片执行 OCR 文字提取，返回清洗后的文本。"""
        result = await self.local_ocr_processor.recognize_image(image_path)
        if not result.ok:
            raise OcrProcessingError(result.error or "OCR recognition failed")

        return result.text.strip()
