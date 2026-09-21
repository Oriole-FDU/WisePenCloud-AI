from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, List, Dict, Optional, Any
from chat.domain.entities import ChatMessage
from chat.domain.entities.message import ToolCallMessage
from chat.domain.entities.provider import ProviderType
from chat.domain.entities.token_usage import TokenUsageSource
from chat.domain.repositories.model_repo import ModelRequestInfo

@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    usage_source: TokenUsageSource = TokenUsageSource.PROVIDER

    def __post_init__(self) -> None:
        self.usage_source = TokenUsageSource(self.usage_source)
        self.input_tokens = max(int(self.input_tokens or 0), 0)
        self.cached_input_tokens = min(max(int(self.cached_input_tokens or 0), 0), self.input_tokens)
        self.output_tokens = max(int(self.output_tokens or 0), 0)

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "TokenUsage") -> None:
        if other is None:
            return
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.cached_input_tokens = min(self.cached_input_tokens, self.input_tokens)

        if self.total_tokens == 0: self.usage_source = other.usage_source
        elif self.usage_source != other.usage_source: self.usage_source = TokenUsageSource.MIXED

    def copy(self) -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            usage_source=self.usage_source,
        )

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
