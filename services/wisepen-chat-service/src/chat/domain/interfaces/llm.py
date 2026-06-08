from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Dict, Optional, Any
from chat.domain.entities import ChatMessage


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
    ) -> Any:
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
    ) -> AsyncGenerator[str, None]:
        yield  # type: ignore[misc]

    @abstractmethod
    async def count_tokens(self, text: str, model_name: str = "gpt-4o") -> int:
        pass
