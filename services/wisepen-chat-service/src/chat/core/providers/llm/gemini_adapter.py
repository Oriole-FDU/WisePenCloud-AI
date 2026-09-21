import base64
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from google import genai
from google.genai import types

from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.provider import ProviderType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import LLMProvider
from chat.domain.interfaces.llm import LLMEventType, LLMStreamEvent, TokenUsage, TokenUsageSource
from chat.domain.entities.message import ToolCallMessage
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.core.exceptions import ServiceException

from .utils import dump_provider_value, read_provider_value


class GeminiAdapter(LLMProvider):
    """
    Gemini 官方 Google GenAI SDK 适配器
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GOOGLE

    def runtime_options_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                    "top_p": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                    "top_k": {"type": "integer", "minimum": 0},
                    "seed": {"type": "integer"},
                    "presence_penalty": {"type": "number"},
                    "frequency_penalty": {"type": "number"},
                    "thinking_config": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "thinking_budget": {"type": "integer"},
                            "thinking_level": {"type": "string", "enum": ["MINIMAL", "LOW", "MEDIUM", "HIGH"]},
                        },
                    },
                },
            },
            "defaults": {
                "temperature": 0.7,
                "thinking_config": {"thinking_budget": -1}, # 模型按任务复杂度自动决定 thinking budget
            },
        }

    async def stream_chat_completion(
        self,
        messages: List[ChatMessage],
        model_request: ModelRequestInfo,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        # 构造 SDK Client（指定 api_key 与 base_url）
        client_kwargs: dict[str, Any] = {"api_key": model_request.api_key}
        if model_request.base_url:
            client_kwargs["http_options"] = types.HttpOptions(base_url=model_request.base_url)
        client = genai.Client(**client_kwargs)

        # 内部消息投影为 Gemini content parts 格式
        contents = self._gemini_contents_formatter(messages)

        # 设置请求参数
        config_kwargs: dict[str, Any] = {
            "tools": self._gemini_tools_formatter(tools), # 工具集
            **model_request.runtime_options
        }

        accumulated_parts: list[Any] = [] # 积累消息
        final_usage: Any = None
        try:
            stream = await client.aio.models.generate_content_stream(
                model=model_request.model_name,
                contents=contents,
                config=types.GenerateContentConfig(**{key: value for key, value in config_kwargs.items() if value is not None}),
            )
            # 流式调用
            async for chunk in stream:
                # Gemini response 里通常有 candidates，当前只取第一个
                content = self._gemini_get_first_content(chunk)
                if content is not None:
                    # 把 SDK 对象转成可 JSON 持久化的结构并累积
                    parts = read_provider_value(content, "parts", []) or []
                    accumulated_parts.extend(dump_provider_value(part) for part in parts)
                    for part in parts:
                        # text part 是文本，包括普通文本和思考增量
                        text = read_provider_value(part, "text")
                        if text:
                            if read_provider_value(part, "thought", False):  # 思考增量
                                yield LLMStreamEvent(type=LLMEventType.REASONING_DELTA, delta=text) # 传递 LLMStreamEvent REASONING_DELTA
                            else:  # 普通文本增量
                                yield LLMStreamEvent(type=LLMEventType.TEXT_DELTA, delta=text) # 传递 LLMStreamEvent TEXT_DELTA

                usage = getattr(chunk, "usage_metadata", None) # 如果收集到 usage_metadata，就是最终的用量
                if usage:
                    final_usage = usage
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Gemini Provider Error: {e}")

        # 计费
        if final_usage:
            input_tokens = int(getattr(final_usage, "prompt_token_count", 0) or 0)
            output_tokens = int(getattr(final_usage, "candidates_token_count", 0) or 0)
            cached_input_tokens = int(getattr(final_usage, "cached_content_token_count", 0) or 0)
            total_tokens = int(getattr(final_usage, "total_token_count", 0) or 0) # 在 input_tokens 与 output_tokens 不可用时备用
            if input_tokens or output_tokens:
                yield LLMStreamEvent(
                    type=LLMEventType.USAGE,
                    usage=TokenUsage(
                        input_tokens=input_tokens,
                        cached_input_tokens=cached_input_tokens,
                        output_tokens=output_tokens,
                    ),
                )
            elif total_tokens:
                yield LLMStreamEvent(type=LLMEventType.USAGE, usage=TokenUsage(output_tokens=total_tokens))

        # 解析工具调用
        calls: list[ToolCallMessage] = []
        for part in accumulated_parts:
            function_call = read_provider_value(part, "function_call")
            # 仅处理 function_call part
            if function_call:
                name = read_provider_value(function_call, "name", "")
                args = read_provider_value(function_call, "args", {}) or {}
                calls.append(ToolCallMessage(
                    call_id=read_provider_value(function_call, "id") or f"call_{uuid.uuid4().hex}",
                    name=name,
                    arguments=args if isinstance(args, dict) else {}
                ))

        if calls: # 传递 LLMStreamEvent TOOL_CALLS
            yield LLMStreamEvent(type=LLMEventType.TOOL_CALLS, tool_calls=calls)
        yield LLMStreamEvent(type=LLMEventType.STATE, provider_payload={"content": accumulated_parts})

    @staticmethod
    def _gemini_contents_formatter(messages: List[ChatMessage]) -> list[dict[str, Any]]:
        # Gemini 使用 contents/parts；system 消息在当前路径下降级为 user text part
        contents: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                contents.append({"role": "user", "parts": [{"text": msg.content or ""}]})
                continue
            # 如果当前消息是 GEMINI 提供的，且存在 provider_payload，则直接取出
            if msg.role == Role.ASSISTANT and msg.model_info and msg.model_info.provider_type == ProviderType.GOOGLE and msg.provider_payload:
                contents.append({
                    "role": "model",
                    "parts": msg.provider_payload["content"]
                })
                continue
            if msg.role == Role.TOOL:
                # 执行工具结果以 role user 的 parts 返回，包含 function_response part 且 name 对应返回的 name
                parts: list[Any] = [{"function_response": {"name": msg.tool_name or "", "response": {"result": msg.content or ""}}}]
                parts.extend(
                    types.Part.from_bytes(data=base64.b64decode(img.base64_data), mime_type=img.media_type)
                    for img in msg.imgs
                )
                contents.append({
                    "role": "user",
                    "parts": parts,
                })
                continue
            # 对于用户消息，或其他非 GEMINI 提供的消息
            role = "model" if msg.role == Role.ASSISTANT else "user"
            parts: list[Any] = [{"text": msg.content or ""}]
            parts.extend(
                types.Part.from_bytes(data=base64.b64decode(img.base64_data), mime_type=img.media_type)
                for img in msg.imgs
            )
            contents.append({"role": role, "parts": parts})
        return contents

    @staticmethod
    def _gemini_tools_formatter(tools: Optional[List[Dict[str, Any]]]) -> Optional[list[types.Tool]]:
        if not tools: return None
        function_declarations = []
        for tool in tools or []:
            fn = tool.get("function", {})
            # 转换为 GEMINI 要求的工具格式
            function_declarations.append({
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return [types.Tool(function_declarations=function_declarations)]

    @staticmethod
    def _gemini_get_first_content(response: Any) -> Any:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            return getattr(candidates[0], "content", None)
        return None
