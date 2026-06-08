from pathlib import Path

from chat.application.tools.web.services.common.file_type_detection.enums import (
    ContentDetectionDetector,
    ContentKind,
    DetectionConfidence,
)
from chat.application.tools.web.services.common.file_type_detection.magika import MagikaDetector
from chat.application.tools.web.services.common.file_type_detection.models import ContentDetection
from chat.application.tools.web.services.common.file_type_detection.zip_classifier import classify_zip_document

_HEADER_BYTES = 64 * 1024
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


_UNKNOWN_BINARY = ContentDetection(
    kind=ContentKind.UNSUPPORTED_BINARY,
    mime_type="application/octet-stream",
    extension=None,
    confidence=DetectionConfidence.UNKNOWN,
    reason="unknown_binary",
    detector=ContentDetectionDetector.FALLBACK_UNKNOWN,
)


class FileTypeDetector:
    """表示当前组件。"""
    def __init__(self, magika_detector: MagikaDetector) -> None:
        self._magika = magika_detector

    async def detect_path(self, path: Path) -> ContentDetection:

        with path.open("rb") as f:
            header = f.read(_HEADER_BYTES)
            if header.startswith(_ZIP_SIGNATURES):
                # 在同一个文件句柄里读完剩余部分，只需一次 open
                return classify_zip_document(header + f.read())

        # 非 ZIP 时直接传 path，Magika 内部自行读取，无需把整个文件加载进内存
        return await self._magika.detect_path(path) or _UNKNOWN_BINARY

    async def detect_bytes(self, content: bytes) -> ContentDetection:

        if content[:_HEADER_BYTES].startswith(_ZIP_SIGNATURES):
            return classify_zip_document(content)

        return await self._magika.detect_bytes(content) or _UNKNOWN_BINARY