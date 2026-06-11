from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, List, Dict, Optional, Any
from chat.domain.entities import ChatMessage

@dataclass
class LLMStreamChunk:
    raw: Any
    usage_tokens: int = 0

@dataclass
class LLMCompletionResult:
    raw: Any
    usage_tokens: int

class LLMProvider(ABC):

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

    @abstractmethod
    async def stream_chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None,
            api_base: Optional[str] = None,
            api_key: Optional[str] = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        yield  # type: ignore[misc]

    @abstractmethod
    async def count_tokens(
            self,
            text: str,
            model_name: str = "gpt-4o"
    ) -> int:
        pass

    @abstractmethod
    async def count_message_tokens(
            self,
            messages: List[ChatMessage],
            model_name: str = "gpt-4o",
            tools: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        pass
