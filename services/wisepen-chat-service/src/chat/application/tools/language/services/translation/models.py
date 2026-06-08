from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True, slots=True)
class TranslationSegment:
    index: int
    source: str
    baseline_translation: str


@dataclass(frozen=True, slots=True)
class TranslationAssistResult:
    source_language: str
    target_language: str
    backend: str
    segments: List[TranslationSegment]
    warnings: List[str] = field(default_factory=list)
