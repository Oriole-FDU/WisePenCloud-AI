from __future__ import annotations

from typing import List, Protocol

from transformers import PreTrainedTokenizerBase

from chat.application.tools.language.services.translation.runtime.segmenter import split_text_for_translation
from .models import TranslationAssistResult, TranslationSegment


class TranslationEngine(Protocol):
    def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str: ...

    async def translate_async(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str: ...

    def get_source_tokenizer(
        self,
        *,
        source_language: str,
        target_language: str,
    ) -> PreTrainedTokenizerBase: ...

    def close(self) -> None: ...


class TranslationAssistService:
    def __init__(
        self,
        engine: TranslationEngine,
        *,
        max_source_tokens: int = 420,
    ) -> None:
        self._engine = engine
        self._max_source_tokens = max_source_tokens

    def assist(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationAssistResult:
        warnings: List[str] = []
        raw_segments = split_text_for_translation(
            text,
            source_lang=source_language,
            tokenizer=self._engine.get_source_tokenizer(
                source_language=source_language,
                target_language=target_language,
            ),
            max_source_tokens=self._max_source_tokens,
        )

        segments: List[TranslationSegment] = []
        for index, segment in enumerate(raw_segments, 1):
            translated = self._engine.translate(
                text=segment,
                source_language=source_language,
                target_language=target_language,
            )
            segments.append(
                TranslationSegment(
                    index=index,
                    source=segment,
                    baseline_translation=translated,
                )
            )

        warnings.append(
            "This is a machine-translation baseline. The assistant should polish the final translation according to context."
        )

        return TranslationAssistResult(
            source_language=source_language,
            target_language=target_language,
            backend="opus_mt",
            segments=segments,
            warnings=warnings,
        )

    async def assist_async(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationAssistResult:
        warnings: List[str] = []
        raw_segments = split_text_for_translation(
            text,
            source_lang=source_language,
            tokenizer=self._engine.get_source_tokenizer(
                source_language=source_language,
                target_language=target_language,
            ),
            max_source_tokens=self._max_source_tokens,
        )

        segments: List[TranslationSegment] = []
        for index, segment in enumerate(raw_segments, 1):
            translated = await self._engine.translate_async(
                text=segment,
                source_language=source_language,
                target_language=target_language,
            )
            segments.append(
                TranslationSegment(
                    index=index,
                    source=segment,
                    baseline_translation=translated,
                )
            )

        warnings.append(
            "This is a machine-translation baseline. The assistant should polish the final translation according to context."
        )

        return TranslationAssistResult(
            source_language=source_language,
            target_language=target_language,
            backend="opus_mt",
            segments=segments,
            warnings=warnings,
        )

    async def close(self) -> None:
        self._engine.close()
