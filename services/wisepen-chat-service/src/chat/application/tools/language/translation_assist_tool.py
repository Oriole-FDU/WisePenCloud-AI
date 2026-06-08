from __future__ import annotations

from typing import Any, Dict

from chat.application.tools.language.services.translation.errors import TranslationAssistError
from chat.application.tools.language.services.translation.formatting import (
    format_translation_error,
    format_translation_result,
)
from chat.application.tools.language.services.translation.service import (
    TranslationAssistService,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

_TOOL_DESCRIPTION = (
    "Provides open-source Chinese-English translation assistance using OPUS-MT / MarianMT. "
    "Use this tool for Chinese-English machine-translation baseline, bilingual comparison, "
    "and long-text segmentation.\n\n"
    "This tool only supports zh <-> en in v1.\n"
    "Use the result as baseline translation evidence; the assistant must polish the final answer."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 8192,
            "description": "Text to translate or check.",
        },
        "source_language": {
            "type": "string",
            "enum": ["zh", "en"],
            "description": "Source language. Supported in v1: zh, en.",
        },
        "target_language": {
            "type": "string",
            "enum": ["zh", "en"],
            "description": "Target language. Supported in v1: zh, en.",
        },
    },
    "required": ["text", "source_language", "target_language"],
    "additionalProperties": False,
}


class TranslationAssistTool(BaseTool):
    def __init__(self, service: TranslationAssistService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "translation_assist"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    @property
    def namespaces(self) -> tuple[str, ...]:
        return ("language",)

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        session_id = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        source_language = kwargs["source_language"]
        target_language = kwargs["target_language"]
        if source_language == target_language:
            return format_translation_error("source_language and target_language must be different.")

        try:
            result = await self._service.assist_async(
                text=kwargs["text"],
                source_language=source_language,
                target_language=target_language,
            )
            return format_translation_result(result)
        except TranslationAssistError as e:
            return format_translation_error(str(e))
        except Exception as e:
            log_fail("translation_assist", repr(e))
            return format_translation_error("An unexpected error occurred during translation.")

    async def close(self) -> None:
        await self._service.close()
