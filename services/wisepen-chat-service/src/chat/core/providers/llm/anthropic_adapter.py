import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from anthropic import AsyncAnthropic

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.provider import ProviderType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import LLMProvider
from chat.domain.interfaces.llm import LLMEventType, LLMStreamEvent, TokenUsage
from chat.domain.entities.message import ToolCallMessage
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.core.exceptions import ServiceException

from .utils import dump_provider_value, without_none


class AnthropicAdapter(LLMProvider):
    """
    Anthropic Messages API 适配器
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.ANTHROPIC

    def runtime_options_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "thinking": {
                        "type": "object",
                        "oneOf": [
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["type", "budget_tokens"],
                                "properties": {
                                    "type": {"type": "string", "const": "enabled"},
                                    "budget_tokens": {"type": "integer", "minimum": 1024},
                                },
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["type"],
                                "properties": {
                                    "type": {"type": "string", "const": "adaptive"},
                                },
                            },
                        ],
                    },
                },
            },
            "defaults": {
                "thinking": {"type": "adaptive"},
            },
        }

    async def stream_chat_completion(
        self,
        messages: List[ChatMessage],
        model_request: ModelRequestInfo,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:

        # 构造 SDK Client（指定 api_key 与 base_url）
        kwargs: dict[str, Any] = {"api_key": model_request.api_key}
        if model_request.base_url:
            kwargs["base_url"] = model_request.base_url
        client = AsyncAnthropic(**kwargs)

        # 内部消息投影为 Anthropic Messages API 格式
        # Anthropic 的 system prompt 不在 messages 数组里，是单独的 system 参数
        anthropic_messages, anthropic_system_message = self._anthropic_messages_formatter(messages)

        # 设置请求参数
        request_kwargs: dict[str, Any] = {
            "model": model_request.model_name, # 模型名
            "messages": anthropic_messages,      # 消息
            "max_tokens": model_request.model.max_output_tokens or settings.CTX_DEFAULT_OUTPUT_RESERVE_TOKENS, # 最大Tokens
            "tools": self._anthropic_tools_formatter(tools), # 工具集
            **model_request.runtime_options
        }
        if anthropic_system_message: # system prompt
            request_kwargs["system"] = anthropic_system_message

        try:
            async with client.messages.stream(**without_none(request_kwargs)) as stream:
                # 流式调用
                async for event in stream:
                    event_type = getattr(event, "type", "")
                    # content_block_delta 是内容块的增量事件，可能是普通文本、工具参数 JSON 片段、thinking 片段等
                    if event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        delta_type = getattr(delta, "type", "")
                        # 文本增量
                        if delta_type == "text_delta":
                            text_delta = getattr(delta, "text", "")
                            if text_delta: # 传递 LLMStreamEvent TEXT_DELTA
                                yield LLMStreamEvent(type=LLMEventType.TEXT_DELTA, delta=text_delta)
                        # 思考增量（只有请求启用 thinking 时才会出现）
                        elif delta_type == "thinking_delta":
                            thinking_delta = getattr(delta, "thinking", "")
                            if thinking_delta: # 传递 LLMStreamEvent REASONING_DELTA
                                yield LLMStreamEvent(type=LLMEventType.REASONING_DELTA, delta=thinking_delta)
                        # 没有处理 tool_use 等增量
                        # 工具调用可在最终完整消息拿到后再解析

                final_message = await stream.get_final_message() # 最终消息
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Anthropic Provider Error: {e}")

        # 计费
        usage = getattr(final_message, "usage", None) # 提取 final_message 的 usage
        raw_input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        cache_creation_input_tokens = int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
        cached_input_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        input_tokens = raw_input_tokens + cache_creation_input_tokens + cached_input_tokens
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        if input_tokens or output_tokens: # 传递 LLMStreamEvent USAGE
            yield LLMStreamEvent(
                type=LLMEventType.USAGE,
                usage=TokenUsage(
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                ),
            )

        # 把 SDK 对象转成可 JSON 持久化的 dict/list
        content_blocks = [dump_provider_value(block) for block in getattr(final_message, "content", [])] # 提取 final_message 的 content

        # 解析工具调用
        calls = []
        for block in content_blocks:
            if block.get("type") != "tool_use": continue # 仅处理 tool_use 块
            calls.append(ToolCallMessage(
                call_id=block.get("id") or f"call_{uuid.uuid4().hex}",
                name=block.get("name") or "",
                arguments=block.get("input") if isinstance(block.get("input"), dict) else {},
            ))

        if calls: # 传递 LLMStreamEvent TOOL_CALLS
            yield LLMStreamEvent(type=LLMEventType.TOOL_CALLS, tool_calls=calls)
        yield LLMStreamEvent(type=LLMEventType.STATE, provider_payload={ "content": content_blocks })

    @staticmethod
    def _anthropic_messages_formatter(messages: List[ChatMessage]) -> tuple[list[dict[str, Any]], str]:
        anthropic_system_message = "\n\n".join(msg.content or "" for msg in messages if msg.role == Role.SYSTEM)

        anthropic_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                continue
            if msg.role == Role.TOOL:
                # 执行工具结果以 role user 的 content 返回，包含 tool_result block 且 tool_use_id 对应返回的 tool_use.id
                # https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
                content = [{"type": "tool_result", "tool_use_id": msg.tool_call_id, "content": msg.content or ""}]
                if msg.imgs:
                    content.extend([
                        {"type": "image",
                         "source": {"type": "base64", "media_type": img.media_type, "data": img.base64_data}}
                        for img in msg.imgs
                    ])
                anthropic_messages.append({
                    "role": "user",
                    "content": content,
                })
                continue
            # 如果当前消息是 ANTHROPIC 提供的，且存在 provider_payload，则直接取出
            if msg.role == Role.ASSISTANT and msg.model_info and msg.model_info.provider_type == ProviderType.ANTHROPIC and msg.provider_payload:
                anthropic_messages.append({
                    "role": "assistant",
                    "content": msg.provider_payload["content"],
                })
                continue
            # 对于用户消息，或其他非 ANTHROPIC 提供的消息
            content = [{"type": "text", "text": msg.content or ""}]
            if msg.imgs:
                content.extend({
                    "type": "image",
                    "source": {"type": "base64", "media_type": img.media_type, "data": img.base64_data}
                } for img in msg.imgs)
            anthropic_messages.append({
                "role": "assistant" if msg.role == Role.ASSISTANT else "user",
                "content": content,
            })
        return anthropic_messages, anthropic_system_message

    @staticmethod
    def _anthropic_tools_formatter(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not tools: return None
        result = []
        for tool in tools:
            function = tool.get("function", {})
            # 转换为 ANTHROPIC 要求的工具格式
            result.append({
                "name": function.get("name"),
                "description": function.get("description"),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            })
        return result
