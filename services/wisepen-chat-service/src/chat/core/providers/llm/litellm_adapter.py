import json
import uuid
from typing import Any, AsyncGenerator, AsyncIterable, Dict, List, Optional, cast

import litellm

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.provider import ProviderType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import LLMProvider
from chat.domain.interfaces.llm import (
    LLMCompletionResult,
    LLMEventType,
    LLMStreamEvent,
    LLMUsage,
    TextCompletionProvider,
)
from chat.domain.entities.message import ToolCallMessage
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.core.exceptions import ServiceException

from .utils import json_object, read_provider_value

litellm.telemetry = False

_is_debug = bootstrap_settings.LOG_LEVEL.upper() == "DEBUG"
litellm.set_verbose = _is_debug
litellm.suppress_debug_info = not _is_debug


class LiteLLMAdapter(LLMProvider, TextCompletionProvider):
    """
    浣跨敤 LiteLLM 搴撶洿鎺ュ湪杩涚▼鍐呰繘琛岄潪閲嶇偣妯″瀷鍜屾櫘閫?OpenAI-compatible fallback 璋冪敤
    api_base / api_key 鍙湪姣忔璋冪敤鏃跺姩鎬佹寚瀹氾紝鏈寚瀹氭椂闄嶇骇鍒板叏灞€ settings
    """

    def __init__(self):
        self._default_api_base = settings.LLM_BASE_URL
        self._default_api_key = settings.LLM_API_KEY

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.LITELLM_OPENAI_COMPATIBLE

    def runtime_options_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                    "top_p": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                    "presence_penalty": {"type": "number", "minimum": -2, "maximum": 2},
                    "frequency_penalty": {"type": "number", "minimum": -2, "maximum": 2},
                    "seed": {"type": "integer"},
                },
            },
            "defaults": {
                "temperature": 0.7,
            },
        }

    @staticmethod
    def _normalize_openai_message(message: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(message)
        if normalized.get("content") is None:
            normalized["content"] = ""
        return normalized

    @staticmethod
    def _legacy_litellm_messages_formatter(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        # LiteLLM fallback 鎸?OpenAI-compatible messages 鎶曞奖锛涢潪 LiteLLM payload 鍙敤鍙鏂囨湰闄嶇骇
        formatted_messages = []
        for message in messages:
            model_info = message.model_info
            # 鍙洖鏀?LiteLLM 鑷繁淇濆瓨鐨?assistant 鍘熺敓娑堟伅锛屽叾浠?provider payload 鍙兘闄嶇骇涓哄彲瑙佹枃鏈?
            if (
                message.role == Role.ASSISTANT
                and model_info is not None
                and model_info.provider_type == ProviderType.LITELLM_OPENAI_COMPATIBLE
                and message.provider_payload
            ):
                payload = LiteLLMAdapter._normalize_openai_message(message.provider_payload["message"])
                if message.reasoning_content and not payload.get("reasoning_content"):
                    payload["reasoning_content"] = message.reasoning_content
                formatted_messages.append(payload)
                continue
            if message.role == Role.TOOL:
                # LiteLLM fallback 浣跨敤 OpenAI-compatible 鐨?role="tool" message
                formatted_messages.append({
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "name": message.tool_name,
                    "content": message.content or "",
                })
                continue
            # 瀵逛簬鐢ㄦ埛娑堟伅锛屾垨鍏朵粬闈?LiteLLM 鎻愪緵鐨勬秷鎭?
            payload = {
                "role": message.role.value,
                "content": message.content or ""
            }
            if message.role == Role.ASSISTANT and message.reasoning_content:
                payload["reasoning_content"] = message.reasoning_content
            formatted_messages.append(payload)
        return formatted_messages

    @staticmethod
    def _tool_call_payloads(tool_calls: Optional[List[ToolCallMessage]]) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        for tool_call in tool_calls or []:
            payloads.append({
                "id": tool_call.call_id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.arguments or {}, ensure_ascii=False, default=str),
                },
            })
        return payloads

    @staticmethod
    def _tool_result_payload(message: ChatMessage) -> Dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.tool_name,
            "content": message.content or "",
        }

    @staticmethod
    def _assistant_payload(message: ChatMessage) -> Dict[str, Any]:
        model_info = message.model_info
        provider_payload = message.provider_payload
        if (
            model_info is not None
            and model_info.provider_type == ProviderType.LITELLM_OPENAI_COMPATIBLE
            and provider_payload
            and provider_payload.get("message")
        ):
            payload = LiteLLMAdapter._normalize_openai_message(provider_payload["message"])
        else:
            payload = {
                "role": "assistant",
                "content": message.content or "",
            }
        if message.reasoning_content and not payload.get("reasoning_content"):
            payload["reasoning_content"] = message.reasoning_content

        # Prefer the internal normalized snapshot so ids still match when a
        # conversation switches from another provider to LiteLLM/OpenAI.
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            payload["tool_calls"] = LiteLLMAdapter._tool_call_payloads(tool_calls)
        return payload

    @staticmethod
    def _litellm_messages_formatter(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        formatted_messages: List[Dict[str, Any]] = []
        i = 0
        while i < len(messages):
            message = messages[i]

            if message.role == Role.ASSISTANT:
                assistant_payload = LiteLLMAdapter._assistant_payload(message)
                tool_calls = assistant_payload.get("tool_calls") or []
                if not tool_calls:
                    formatted_messages.append(assistant_payload)
                    i += 1
                    continue

                expected_ids = {call.get("id") for call in tool_calls if call.get("id")}
                matched_ids: set[str] = set()
                tool_results: List[Dict[str, Any]] = []
                j = i + 1
                while j < len(messages) and messages[j].role == Role.TOOL:
                    tool_call_id = messages[j].tool_call_id
                    if tool_call_id and tool_call_id in expected_ids:
                        matched_ids.add(tool_call_id)
                        tool_results.append(LiteLLMAdapter._tool_result_payload(messages[j]))
                    j += 1

                valid_tool_calls = [
                    call for call in tool_calls
                    if call.get("id") in matched_ids
                ]
                if valid_tool_calls:
                    assistant_payload["tool_calls"] = valid_tool_calls
                    formatted_messages.append(assistant_payload)
                    formatted_messages.extend(tool_results)
                else:
                    assistant_payload.pop("tool_calls", None)
                    if assistant_payload.get("content"):
                        formatted_messages.append(assistant_payload)

                i = j
                continue

            if message.role == Role.TOOL:
                # OpenAI-compatible chat rejects orphan tool messages. They can
                # appear after Redis/window trimming, so skip them at the boundary.
                i += 1
                continue

            formatted_messages.append({
                "role": message.role.value,
                "content": message.content or "",
            })
            i += 1

        return formatted_messages

    @staticmethod
    def _to_openai_compatible_model(model_name: str) -> str:
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
        # 鍐呴儴娑堟伅鎶曞奖涓?OpenAI-compatible message 鏍煎紡
        formatted_messages = self._litellm_messages_formatter(messages)
        litellm_model = self._to_openai_compatible_model(model_name)
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
            token_usage = getattr(usage, "total_tokens", 0) if usage else 0
            content = response.choices[0].message.content or ""
            return LLMCompletionResult(content=content, token_usage=int(token_usage), raw=response)

        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=str(e))

    async def stream_chat_completion(
            self,
            messages: List[ChatMessage],
            model_request: ModelRequestInfo,
            tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:

        # 鍐呴儴娑堟伅鎶曞奖涓?OpenAI-compatible message 鏍煎紡
        formatted_msgs = self._litellm_messages_formatter(messages)
        litellm_model = self._to_openai_compatible_model(model_request.model_name)

        # 璁剧疆璇锋眰鍙傛暟
        # LiteLLM 浣滀负 fallback 璺緞锛宼ools 缁х画閫忎紶 OpenAI-compatible schema
        token_usage = 0
        tool_acc: dict[int, dict[str, str]] = {}
        try:
            response = await litellm.acompletion(
                model=litellm_model, # 妯″瀷鍚?
                messages=formatted_msgs, # 娑堟伅
                stream=True,
                stream_options={"include_usage": True},
                tools=tools, # 宸ュ叿闆?
                drop_params=True,
                api_base=model_request.base_url or self._default_api_base,
                api_key=model_request.api_key or self._default_api_key,
                **model_request.runtime_options,
            )
            stream = cast(AsyncIterable[Any], response)
            assistant_text = ""
            reasoning_text = ""

            # 娴佸紡璋冪敤
            async for chunk in stream:
                # 濡傛灉鏈 response.usage.total_tokens 鏈夊€硷紝灏辨洿鏂?token_usage锛屽惁鍒欎繚鐣欎箣鍓嶇殑 token_usage
                usage = read_provider_value(chunk, "usage", {}) or {}
                token_usage = int(read_provider_value(usage, "total_tokens", token_usage) or token_usage)

                # Qwen response 閲岄€氬父鏈?candidates锛屽綋鍓嶅彧鍙栫涓€涓?
                choices = read_provider_value(chunk, "choices", None) or []
                if not choices: continue
                delta = read_provider_value(choices[0], "delta", {}) or {}
                # 鎬濊€冨閲?
                reasoning = read_provider_value(delta, "reasoning_content")
                if reasoning: # 浼犻€?LLMStreamEvent REASONING_DELTA
                    reasoning_text += reasoning
                    yield LLMStreamEvent(type=LLMEventType.REASONING_DELTA, delta=reasoning)
                # 鏂囨湰澧為噺
                if getattr(delta, "content", None):
                    assistant_text += delta.content
                    yield LLMStreamEvent(type=LLMEventType.TEXT_DELTA, delta=delta.content) # 浼犻€?LLMStreamEvent TEXT_DELTA
                # 宸ュ叿璋冪敤鍙傛暟鐨勫閲?
                if getattr(delta, "tool_calls", None):
                    for tool_call_delta in delta.tool_calls: # 鍒嗙墖绉疮
                        idx = tool_call_delta.index
                        acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        # 鎸?index 鎵惧埌瀵瑰簲 accumulator
                        if tool_call_delta.id: # 绱姞 id锛堝鏋滄湁锛?
                            acc["id"] = tool_call_delta.id
                        if tool_call_delta.function: # 绱姞 name
                            if tool_call_delta.function.name: # 绱姞 name
                                acc["name"] += tool_call_delta.function.name
                            if tool_call_delta.function.arguments: # 绱姞 arguments
                                acc["arguments"] += tool_call_delta.function.arguments

        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=str(e))

        # 璁¤垂
        if token_usage:  # 浼犻€?LLMStreamEvent USAGE
            yield LLMStreamEvent(type=LLMEventType.USAGE, usage=LLMUsage(output_tokens=int(token_usage)))

        # 瑙ｆ瀽宸ュ叿璋冪敤
        tool_calls: list[ToolCallMessage] = []
        tool_call_payloads = []
        for idx in sorted(tool_acc.keys()):
            acc = tool_acc[idx]
            call_id = acc["id"] or f"call_{uuid.uuid4().hex}"
            tool_call_payloads.append({
                "id": call_id,
                "type": "function",
                "function": {"name": acc["name"], "arguments": acc["arguments"]},
            })
            tool_calls.append(ToolCallMessage(
                call_id=call_id,
                name=acc["name"],
                arguments=json_object(acc["arguments"])
            ))
        if tool_calls:
            yield LLMStreamEvent(type=LLMEventType.TOOL_CALLS, tool_calls=tool_calls)

        # 淇濆瓨 OpenAI-compatible assistant message锛屼緵涓嬩竴杞崗璁洖鏀?
        assistant_message = {
            "role": "assistant",
            "content": assistant_text or "",
        }
        if reasoning_text:
            assistant_message["reasoning_content"] = reasoning_text
        if tool_call_payloads:
            assistant_message["tool_calls"] = tool_call_payloads
        yield LLMStreamEvent(type=LLMEventType.STATE, provider_payload={ "message": assistant_message })
