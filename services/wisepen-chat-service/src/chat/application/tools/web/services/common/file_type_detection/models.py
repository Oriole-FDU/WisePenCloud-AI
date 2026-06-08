from dataclasses import dataclass
from typing import Optional

from chat.application.tools.web.services.common.file_type_detection.enums import (
    ContentDetectionDetector,
    ContentKind,
    DetectionConfidence,
)


@dataclass(frozen=True, slots=True)
class ContentDetection:
    kind: ContentKind
    mime_type: str
    extension: Optional[str]
    confidence: DetectionConfidence
    reason: str
    detector: ContentDetectionDetector
