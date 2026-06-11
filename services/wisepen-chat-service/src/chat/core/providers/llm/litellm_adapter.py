import json

import litellm
from typing import AsyncGenerator, List, Dict, Optional, Any, cast, AsyncIterable
from chat.domain.entities import ChatMessage
from chat.domain.interfaces import LLMProvider
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces.llm import LLMStreamChunk, LLMCompletionResult
from common.core.exceptions import ServiceException
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings

litellm.telemetry = False

_is_debug = bootstrap_settings.LOG_LEVEL.upper() == "DEBUG"
litellm.set_verbose = _is_debug
litellm.suppress_debug_info = not _is_debug


class LiteLLMAdapter(LLMProvider):
    """
    使用 LiteLLM 库直接在进程内进行模型路由和调用。
    api_base / api_key 可在每次调用时动态指定，未指定时降级到全局 settings。
    """

    def __init__(self):
        self._default_api_base = settings.LLM_BASE_URL
        self._default_api_key = settings.LLM_API_KEY

    @staticmethod
    def _convert_messages(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        formatted_messages = []
        for message in messages:
            payload = {"role": message.role.value, "content": message.content}
            if getattr(message, "tool_calls", None):
                payload["tool_calls"] = message.tool_calls
            if getattr(message, "tool_call_id", None):
                payload["tool_call_id"] = message.tool_call_id
            if getattr(message, "name", None):
                payload["name"] = message.name
            formatted_messages.append(payload)
        return formatted_messages

    def _format_model_for_litellm(self, model_name: str) -> str:
        if "/" in model_name:
            return model_name
        return f"openai/{model_name}"

    async def chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None,
            api_base: Optional[str] = None,
            api_key: Optional[str] = None,
    ) -> LLMCompletionResult:
        formatted_messages = self._convert_messages(messages)
        litellm_model = self._format_model_for_litellm(model_name)
        try:
            response = await litellm.acompletion(
                model=litellm_model,
                messages=formatted_messages,
                stream=False,
                temperature=temperature,
                tools=tools,
                drop_params=True,
                api_base=api_base or self._default_api_base,
                api_key=api_key or self._default_api_key,
            )
            usage = getattr(response, "usage", None)
            usage_tokens = getattr(usage, "total_tokens", 0) if usage else 0
            return LLMCompletionResult(raw=response, usage_tokens=int(usage_tokens))

        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}")

    async def stream_chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None,
            api_base: Optional[str] = None,
            api_key: Optional[str] = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:

        formatted_msgs = self._convert_messages(messages)
        litellm_model = self._format_model_for_litellm(model_name)

        try:
            response = await litellm.acompletion(
                model=litellm_model,
                messages=formatted_msgs,
                stream=True,
                stream_options={"include_usage": True},
                temperature=temperature,
                tools=tools,
                drop_params=True,
                api_base=api_base or self._default_api_base,
                api_key=api_key or self._default_api_key,
            )
            stream = cast(AsyncIterable[Any], response)

            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                usage_tokens = getattr(usage, "total_tokens", 0) if usage else 0
                yield LLMStreamChunk(raw=chunk, usage_tokens=int(usage_tokens))

        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}")

    async def count_tokens(
            self,
            text: str,
            model_name: str = "gpt-4o"
    ) -> int:
        try:
            litellm_model = self._format_model_for_litellm(model_name)
            return litellm.token_counter(model=litellm_model, text=text)
        except Exception:
            return len(text)

    async def count_message_tokens(
            self,
            messages: List[ChatMessage],
            model_name: str = "gpt-4o",
            tools: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        try:
            formatted_messages = self._convert_messages(messages)
            litellm_model = self._format_model_for_litellm(model_name)
            result = await litellm.acount_tokens(
                model=litellm_model,
                messages=formatted_messages,
                tools=tools,
            )
            return int(getattr(result, "total_tokens", 0) or 0)
        except Exception:
            return len(json.dumps(messages, ensure_ascii=False))