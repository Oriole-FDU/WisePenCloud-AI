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
    使用 LiteLLM 库直接在进程内进行非重点模型和普通 OpenAI-compatible fallback 调用
    api_base / api_key 可在每次调用时动态指定，未指定时降级到全局 settings
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
        # LiteLLM fallback 按 OpenAI-compatible messages 投影；非 LiteLLM payload 只用可见文本降级
        formatted_messages = []
        for message in messages:
            model_info = message.model_info
            # 只回放 LiteLLM 自己保存的 assistant 原生消息，其他 provider payload 只能降级为可见文本
            if (
                message.role == Role.ASSISTANT
                and model_info is not None
                and model_info.provider_type == ProviderType.LITELLM_OPENAI_COMPATIBLE
                and message.provider_payload
            ):
                formatted_messages.append(
                    LiteLLMAdapter._normalize_openai_message(message.provider_payload["message"])
                )
                continue
            if message.role == Role.TOOL:
                # LiteLLM fallback 使用 OpenAI-compatible 的 role="tool" message
                formatted_messages.append({
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "name": message.tool_name,
                    "content": message.content or "",
                })
                continue
            # 对于用户消息，或其他非 LiteLLM 提供的消息
            formatted_messages.append({
                "role": message.role.value,
                "content": message.content or ""
            })
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
        # 内部消息投影为 OpenAI-compatible message 格式
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

        # 内部消息投影为 OpenAI-compatible message 格式
        formatted_msgs = self._litellm_messages_formatter(messages)
        litellm_model = self._to_openai_compatible_model(model_request.model_name)

        # 设置请求参数
        # LiteLLM 作为 fallback 路径，tools 继续透传 OpenAI-compatible schema
        token_usage = 0
        tool_acc: dict[int, dict[str, str]] = {}
        try:
            response = await litellm.acompletion(
                model=litellm_model, # 模型名
                messages=formatted_msgs, # 消息
                stream=True,
                stream_options={"include_usage": True},
                tools=tools, # 工具集
                drop_params=True,
                api_base=model_request.base_url or self._default_api_base,
                api_key=model_request.api_key or self._default_api_key,
                **model_request.runtime_options,
            )
            stream = cast(AsyncIterable[Any], response)
            assistant_text = ""

            # 流式调用
            async for chunk in stream:
                # 如果本次 response.usage.total_tokens 有值，就更新 token_usage，否则保留之前的 token_usage
                usage = read_provider_value(chunk, "usage", {}) or {}
                token_usage = int(read_provider_value(usage, "total_tokens", token_usage) or token_usage)

                # Qwen response 里通常有 candidates，当前只取第一个
                choices = read_provider_value(chunk, "choices", None) or []
                if not choices: continue
                delta = read_provider_value(choices[0], "delta", {}) or {}
                # 思考增量
                reasoning = read_provider_value(delta, "reasoning_content")
                if reasoning: # 传递 LLMStreamEvent REASONING_DELTA
                    yield LLMStreamEvent(type=LLMEventType.REASONING_DELTA, delta=reasoning)
                # 文本增量
                if getattr(delta, "content", None):
                    assistant_text += delta.content
                    yield LLMStreamEvent(type=LLMEventType.TEXT_DELTA, delta=delta.content) # 传递 LLMStreamEvent TEXT_DELTA
                # 工具调用参数的增量
                if getattr(delta, "tool_calls", None):
                    for tool_call_delta in delta.tool_calls: # 分片积累
                        idx = tool_call_delta.index
                        acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        # 按 index 找到对应 accumulator
                        if tool_call_delta.id: # 累加 id（如果有）
                            acc["id"] = tool_call_delta.id
                        if tool_call_delta.function: # 累加 name
                            if tool_call_delta.function.name: # 累加 name
                                acc["name"] += tool_call_delta.function.name
                            if tool_call_delta.function.arguments: # 累加 arguments
                                acc["arguments"] += tool_call_delta.function.arguments

        except litellm.ContextWindowExceededError:
            raise ServiceException(ChatErrorCode.CONTEXT_LIMIT_EXCEEDED)
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=str(e))

        # 计费
        if token_usage:  # 传递 LLMStreamEvent USAGE
            yield LLMStreamEvent(type=LLMEventType.USAGE, usage=LLMUsage(output_tokens=int(token_usage)))

        # 解析工具调用
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

        # 保存 OpenAI-compatible assistant message，供下一轮协议回放
        assistant_message = {
            "role": "assistant",
            "content": assistant_text or "",
        }
        if tool_call_payloads:
            assistant_message["tool_calls"] = tool_call_payloads
        yield LLMStreamEvent(type=LLMEventType.STATE, provider_payload={ "message": assistant_message })
