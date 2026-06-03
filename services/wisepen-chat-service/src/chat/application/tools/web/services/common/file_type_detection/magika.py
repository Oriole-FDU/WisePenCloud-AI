import asyncio
from pathlib import Path
from typing import Any, NamedTuple, Optional

from chat.application.tools.web.services.common.file_type_detection.enums import (
    ContentDetectionDetector,
    ContentKind,
    DetectionConfidence,
)
from chat.application.tools.web.services.common.file_type_detection.models import ContentDetection

# Magika 信任分数阈值
_HIGH_CONFIDENCE_THRESHOLD = 0.75

# 特殊匹配和分类的规则集合
_ZIP_MIME_TYPES = {"application/zip", "application/x-zip-compressed"}
_HTML_EXTENSIONS = {".html", ".htm"}
_XML_MIME_TYPES = {"application/xml", "text/xml"}
_DOCUMENT_EXTENSIONS = {
    ".doc",  ".docx", ".docm",
    ".xls",  ".xlsx", ".xlsm",
    ".ppt",  ".pptx", ".pptm",
    ".epub", ".ods",
}
_MEDIA_MIME_PREFIXES = ("audio/", "video/")


class ContentKindMapping(NamedTuple):
    """内部使用的中间类型映射结构"""
    kind: ContentKind
    mime_type: str
    extension: Optional[str]


class MagikaDetector:
    """基于 Google Magika (AI) 的文件类型检测器"""

    def __init__(self) -> None:
        """初始化对象依赖。"""
        from magika import Magika
        self._magika: Any = Magika()

    async def detect_path(self, path: Path) -> Optional[ContentDetection]:
        """异步检测指定路径文件的类型"""
        result = await asyncio.to_thread(self._magika.identify_path, path)
        return self._map_result(result)

    async def detect_bytes(self, content: bytes) -> Optional[ContentDetection]:
        """异步检测二进制字节流的类型"""
        result = await asyncio.to_thread(self._magika.identify_bytes, content)
        return self._map_result(result)

    def _map_result(self, result: Any) -> Optional[ContentDetection]:
        """将 Magika 的原生返回结果映射为统一的 ContentDetection 模型"""
        # 低置信度结果直接拒绝
        if not result.ok or result.score < _HIGH_CONFIDENCE_THRESHOLD:
            return None

        output = result.output
        label: str = output.label
        mime_type: str = output.mime_type
        extension: Optional[str] = f".{output.extensions[0]}" if output.extensions else None

        # ZIP 容器格式过滤，交由专用压缩包分类器进行深度解析。
        if "zip" in label or mime_type in _ZIP_MIME_TYPES or extension == ".zip":
            return None

        # 映射内部核心类型
        mapping = _map_mime_or_extension(mime_type, extension, label)
        if mapping is None:
            return None

        return ContentDetection(
            kind=mapping.kind,
            mime_type=mapping.mime_type,
            extension=mapping.extension or extension,
            confidence=DetectionConfidence.AI,
            reason=f"magika_{label or mime_type}",
            detector=ContentDetectionDetector.MAGIKA,
        )


def _map_mime_or_extension(
    mime_type: str,
    extension: Optional[str],
    label: str,
) -> Optional[ContentKindMapping]:
    """通过 MIME 类型、后缀名或 Magika 标签映射具体的 ContentKind 策略"""
    # 1. 优先匹配 PDF 及常见文档格式
    if mime_type == "application/pdf" or extension == ".pdf":
        return ContentKindMapping(ContentKind.DOCUMENT, "application/pdf", ".pdf")

    if extension in _DOCUMENT_EXTENSIONS:
        return ContentKindMapping(ContentKind.DOCUMENT, mime_type, extension)

    # 2. 多媒体与结构化文本匹配
    if mime_type.startswith("image/"):
        return ContentKindMapping(ContentKind.IMAGE, mime_type, extension)

    if mime_type == "text/html" or extension in _HTML_EXTENSIONS:
        return ContentKindMapping(ContentKind.HTML, "text/html", ".html")

    if mime_type == "application/json" or mime_type.endswith("+json") or extension == ".json":
        return ContentKindMapping(ContentKind.JSON, "application/json", ".json")

    if mime_type in _XML_MIME_TYPES or mime_type.endswith("+xml") or extension == ".xml":
        return ContentKindMapping(ContentKind.XML, "application/xml", ".xml")

    # 3. 基础文本流（置于结构化文本之后防止误吞）
    if mime_type.startswith("text/") or "text" in label:
        return ContentKindMapping(ContentKind.TEXT, "text/plain", ".txt")

    # 4. 音视频媒体兜底
    if mime_type.startswith(_MEDIA_MIME_PREFIXES):
        return ContentKindMapping(ContentKind.UNSUPPORTED_MEDIA, mime_type, extension)

    return None
