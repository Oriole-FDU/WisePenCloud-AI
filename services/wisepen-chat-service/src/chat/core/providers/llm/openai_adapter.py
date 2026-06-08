from typing import AsyncGenerator, List, Dict, Optional, Any
from openai import AsyncOpenAI, BadRequestError
import tiktoken

from chat.domain.entities import ChatMessage
from chat.domain.interfaces import LLMProvider
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from chat.core.config.app_settings import settings


class OpenAIAdapter(LLMProvider):
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            timeout=120.0,
            max_retries=2
        )

    def _convert_messages(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in messages:
            payload = {"role": msg.role.value, "content": msg.content}

            # 提取 Tool Calling 相关字段
            if getattr(msg, "tool_calls", None):
                payload["tool_calls"] = msg.tool_calls
            if getattr(msg, "tool_call_id", None):
                payload["tool_call_id"] = msg.tool_call_id
            if getattr(msg, "name", None):
                payload["name"] = msg.name

            formatted.append(payload)

        return formatted

    async def chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None
    ) -> Any:
        formatted_msgs = self._convert_messages(messages)

        # 组装请求参数，忽略 None 值的 tools
        kwargs = {
            "model": model_name,
            "messages": formatted_msgs,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message

        except BadRequestError as e:
            # 识别上下文超限错误
            if "context_length_exceeded" in str(e):
                raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Bad Request: {e}")
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}")

    async def stream_chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None
    ) -> AsyncGenerator[Any, None]:

        formatted_msgs = self._convert_messages(messages)

        kwargs = {
            "model": model_name,
            "messages": formatted_msgs,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            response_stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in response_stream:
                # 原封不动地将 OpenAI 的 chunk yield 出去，
                # QueryLoopRuntime 中的 delta.content 和 delta.tool_calls 解析完全兼容
                yield chunk

        except BadRequestError as e:
            if "context_length_exceeded" in str(e):
                raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Bad Request: {e}")
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}")

    async def count_tokens(self, text: str, model_name: str = "gpt-4o") -> int:
        """
        利用 tiktoken 计算准确的 Token 数量
        """
        if not text:
            return 0

        try:
            # 尝试获取模型对应的编码器
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            # 如果是其他模型（如 gemini, claude 等），统一下降级使用 cl100k_base 估算
            encoding = tiktoken.get_encoding("cl100k_base")

        return len(encoding.encode(text))