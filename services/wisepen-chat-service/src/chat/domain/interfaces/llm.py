from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, List, Dict, Optional, Any
from chat.domain.entities import ChatMessage
from chat.domain.entities.message import ToolCallMessage
from chat.domain.entities.provider import ProviderType
from chat.application.token_usage import TokenUsage
from chat.domain.repositories.model_repo import ModelRequestInfo

@dataclass
class LLMCompletionResult:
    content: str
    usage: TokenUsage
    raw: Any = None

class LLMEventType(str, Enum):
    TEXT_DELTA = "TEXT_DELTA"
    REASONING_DELTA = "REASONING_DELTA"
    TOOL_CALLS = "TOOL_CALLS"
    USAGE = "USAGE"
    STATE = "STATE"

@dataclass
class LLMStreamEvent:
    type: LLMEventType
    delta: str | None = None
    tool_calls: list[ToolCallMessage] | None = None
    usage: TokenUsage | None = None
    provider_payload: dict[str, Any] | None = None
    response_id: str | None = None

class LLMProvider(ABC):
    @staticmethod
    def empty_runtime_options_manifest() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
            "defaults": {},
        }

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        pass

    def supports_tools(self) -> bool:
        return True

    def runtime_options_manifest(self) -> dict[str, Any]:
        return self.empty_runtime_options_manifest()

    @abstractmethod
    async def stream_chat_completion(
            self,
            messages: List[ChatMessage],
            model_request: ModelRequestInfo,
            tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        yield  # type: ignore[misc]

class TextCompletionProvider(ABC):
    @abstractmethod
    async def chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None,
            api_base: Optional[str] = None,
            api_key: Optional[str] = None,
    ) -> LLMCompletionResult:
        pass
