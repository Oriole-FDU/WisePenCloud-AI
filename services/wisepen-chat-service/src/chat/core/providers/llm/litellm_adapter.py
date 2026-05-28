import litellm
from typing import AsyncGenerator, List, Dict, Optional, Any
from chat.domain.entities import ChatMessage
from chat.domain.interfaces import LLMProvider
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from chat.core.config.app_settings import settings

# 遥测关闭
litellm.telemetry = False

# verbose / suppress_debug_info 跟随 LOG_LEVEL：非 DEBUG 时完全静默
_is_debug = settings.LOG_LEVEL.upper() == "DEBUG"
litellm.set_verbose = _is_debug
litellm.suppress_debug_info = not _is_debug


class LiteLLMAdapter(LLMProvider):
    """
    使用 LiteLLM 库直接在进程内进行模型路由和调用，
    统一经由 settings.LLM_BASE_URL 网关转发。
    """

    def __init__(self):
        litellm.api_base = settings.LLM_BASE_URL
        litellm.api_key = settings.LLM_API_KEY

    def _convert_messages(self, messages: List[ChatMessage]) -> List[Dict[str, str]]:
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

    async def chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None
    ) -> Any:
        formatted_msgs = self._convert_messages(messages)
        try:
            response = await litellm.acompletion(
                model=model_name,
                messages=formatted_msgs,
                stream=False,  # 明确关闭流式
                temperature=temperature,
                drop_params=True
            )
            return response.choices[0].message
        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}")

    async def stream_chat_completion(
            self,
            messages: List[ChatMessage],
            model_name: str,
            temperature: float = 0.7,
            tools: Optional[List[Dict[str, Any]]] = None
    ) -> AsyncGenerator[str, None]:

        formatted_msgs = self._convert_messages(messages)

        try:
            response = await litellm.acompletion(
                model=model_name,
                messages=formatted_msgs,
                stream=True,
                temperature=temperature,
                tools=tools,
                drop_params=True
            )
            async for chunk in response:
                yield chunk

        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Provider Error: {e}")

    async def count_tokens(self, text: str, model_name: str = "gpt-4o") -> int:
        """
        利用 LiteLLM 自带的 tokenizer，非常准确
        """
        try:
            return litellm.token_counter(model=model_name, text=text)
        except:
            # 降级策略
            return len(text)
