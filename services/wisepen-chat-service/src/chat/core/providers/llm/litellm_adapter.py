from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import litellm

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.domain.entities import ChatMessage
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import LLMProvider
from common.core.exceptions import ServiceException

litellm.telemetry = False

_is_debug = bootstrap_settings.LOG_LEVEL.upper() == "DEBUG"
litellm.set_verbose = _is_debug
litellm.suppress_debug_info = not _is_debug


_DISABLE_PARALLEL_TOOL_CALL_NAMES: Tuple[str, ...] = ("browse_interact", "web_search")


def _should_disable_parallel_tool_calls(tools: Optional[List[Dict[str, Any]]]) -> bool:
    if not tools:
        return False
    disabled_names = set(_DISABLE_PARALLEL_TOOL_CALL_NAMES)
    for tool in tools:
        function = tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name in disabled_names:
                return True
    return False


class LiteLLMAdapter(LLMProvider):
    """
    使用 LiteLLM 库直接在进程内进行模型路由和调用。
    api_base / api_key 可在每次调用时动态指定，未指定时降级到全局 settings。
    """

    def __init__(self):
        self._default_api_base = settings.LLM_BASE_URL
        self._default_api_key = settings.LLM_API_KEY

    def _convert_messages(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in messages:
            payload = {"role": msg.role.value, "content": msg.content}

            if getattr(msg, "tool_calls", None):
                payload["tool_calls"] = msg.tool_calls
            if getattr(msg, "tool_call_id", None):
                payload["tool_call_id"] = msg.tool_call_id
            if getattr(msg, "name", None):
                payload["name"] = msg.name

            formatted.append(payload)

        return formatted

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
    ):
        formatted_msgs = self._convert_messages(messages)
        litellm_model = self._format_model_for_litellm(model_name)

        completion_kwargs: Dict[str, Any] = {
            "model": litellm_model,
            "messages": formatted_msgs,
            "stream": False,
            "temperature": temperature,
            "drop_params": True,
            "api_base": api_base or self._default_api_base,
            "api_key": api_key or self._default_api_key,
        }

        if tools:
            completion_kwargs["tools"] = tools

        if _should_disable_parallel_tool_calls(tools):
            completion_kwargs["parallel_tool_calls"] = False

        try:
            response = await litellm.acompletion(**completion_kwargs)
            return response.choices[0].message
        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(
                ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}"
            )

    async def stream_chat_completion(
        self,
        messages: List[ChatMessage],
        model_name: str,
        temperature: float = 0.7,
        tools: Optional[List[Dict[str, Any]]] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:

        formatted_msgs = self._convert_messages(messages)
        litellm_model = self._format_model_for_litellm(model_name)

        completion_kwargs: Dict[str, Any] = {
            "model": litellm_model,
            "messages": formatted_msgs,
            "stream": True,
            "temperature": temperature,
            "drop_params": True,
            "api_base": api_base or self._default_api_base,
            "api_key": api_key or self._default_api_key,
        }

        if tools:
            completion_kwargs["tools"] = tools

        if _should_disable_parallel_tool_calls(tools):
            completion_kwargs["parallel_tool_calls"] = False

        try:
            response = await litellm.acompletion(**completion_kwargs)
            async for chunk in response:
                yield chunk

        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(
                ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}"
            )

    async def count_tokens(self, text: str, model_name: str = "gpt-4o") -> int:
        try:
            return litellm.token_counter(model=model_name, text=text)
        except Exception:
            return len(text)
