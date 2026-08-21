import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import dashscope

from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.provider import ProviderType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import LLMProvider
from chat.domain.interfaces.llm import LLMEventType, LLMStreamEvent, LLMUsage
from chat.domain.entities.message import ToolCallMessage
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.core.exceptions import ServiceException

from .utils import json_object, read_provider_value, without_none


class QwenAdapter(LLMProvider):
    """
    Qwen 官方阿里云百炼 DashScope Python SDK 适配器
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.ALIBABA

    def runtime_options_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "temperature": {"type": "number", "minimum": 0, "exclusiveMaximum": 2},
                    "top_p": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                    "top_k": {"type": "integer", "minimum": 0},
                    "enable_thinking": {"type": "boolean"},
                    "thinking_budget": {"type": "integer", "minimum": 0},
                    "presence_penalty": {"type": "number", "minimum": -2, "maximum": 2},
                    "repetition_penalty": {"type": "number", "minimum": 0},
                    "seed": {"type": "integer"},
                },
            },
            "defaults": {
                "temperature": 0.7,
                "enable_thinking": True,
            },
        }

    async def stream_chat_completion(
        self,
        messages: List[ChatMessage],
        model_request: ModelRequestInfo,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        # DashScope 的调用接口由模型能力决定；视觉模型即使本轮没有图片，也必须使用多模态协议。
        qwen_messages = self._qwen_messages_formatter(
            messages,
            multimodal=model_request.model.support_vision,
        )

        # 设置请求参数
        request_kwargs:dict[str, Any] = {
            # DashScope SDK 通过 base_address 覆盖默认 endpoint；未配置时保留 SDK 默认地址。
            "base_address": model_request.base_url,
            "api_key": model_request.api_key, # 鉴权密钥
            "model": model_request.model_name, # 模型名
            "messages": qwen_messages, # 消息
            "result_format": "message",
            "stream": True,
            "incremental_output": True,
            "tools": tools, # Qwen function calling 使用 OpenAI-compatible tools schema，无需转换
            **model_request.runtime_options
        }

        assistant_text = ""
        reasoning_text = ""
        tool_calls: list[ToolCallMessage] = []
        tool_call_payloads = []
        token_usage = 0
        try:
            # 上游已用 support_vision 拦截不支持视觉的图片请求，这里沿用同一能力边界选接口。
            call = (
                dashscope.MultiModalConversation.call
                if model_request.model.support_vision
                else dashscope.Generation.call
            )
            responses = call(**without_none(request_kwargs))
            # 流式调用
            for response in responses:
                status_code = read_provider_value(response, "status_code")
                if status_code and int(status_code) >= 400:
                    code = read_provider_value(response, "code", "")
                    message = read_provider_value(response, "message", "DashScope request failed")
                    raise ServiceException(
                        ChatErrorCode.LLM_GENERATION_FAILED,
                        custom_msg=(
                            f"Qwen Provider Error: status={status_code}, "
                            f"code={code}, message={message}"
                        ),
                    )

                output = read_provider_value(response, "output", {}) or {}

                # 如果本次 response.usage.total_tokens 有值，就更新 token_usage，否则保留之前的 token_usage
                usage = read_provider_value(response, "usage", {}) or {}
                token_usage = int(read_provider_value(usage, "total_tokens", token_usage) or token_usage)

                # Qwen response 里通常有 candidates，当前只取第一个
                choices = read_provider_value(output, "choices", []) or []
                if not choices: continue
                message = read_provider_value(choices[0], "message", {}) or {}
                # 思考增量
                reasoning = read_provider_value(message, "reasoning_content")
                if reasoning:
                    reasoning_text += reasoning
                    yield LLMStreamEvent(type=LLMEventType.REASONING_DELTA, delta=reasoning) # 传递 LLMStreamEvent REASONING_DELTA
                # 文本增量
                content = read_provider_value(message, "content")
                if isinstance(content, list):
                    content = "".join(
                        str(read_provider_value(part, "text", "") or "")
                        for part in content
                    )
                if content:
                    assistant_text += content
                    yield LLMStreamEvent(type=LLMEventType.TEXT_DELTA, delta=content) # 传递 LLMStreamEvent TEXT_DELTA

                # 当 message.tool_calls 出现时,function.arguments 已经是一个可解析的完整 JSON 字符串
                # https://help.aliyun.com/zh/model-studio/qwen-api-via-dashscope
                for call in read_provider_value(message, "tool_calls", []) or []:
                    payload = call if isinstance(call, dict) else {}

                    # 保存完整 tool_call 原生块
                    function = payload.get("function", {})
                    tool_call_payloads.append(payload)
                    tool_calls.append(ToolCallMessage(
                        call_id=payload.get("id") or f"call_{uuid.uuid4().hex}",
                        name=function.get("name", ""),
                        arguments=json_object(function.get("arguments", "{}")),
                    ))
        except ServiceException:
            raise
        except Exception as e:
            raise ServiceException(ChatErrorCode.LLM_GENERATION_FAILED, custom_msg=f"Qwen Provider Error: {e}")

        # 计费
        if token_usage: # 传递 LLMStreamEvent USAGE
            yield LLMStreamEvent(type=LLMEventType.USAGE, usage=LLMUsage(output_tokens=token_usage))

        # 解析工具调用
        if tool_calls: # 传递 LLMStreamEvent TOOL_CALLS
            yield LLMStreamEvent(type=LLMEventType.TOOL_CALLS, tool_calls=tool_calls)

        # 保存 Qwen 原生 assistant message，供下一轮协议回放
        assistant_message = {
            "role": "assistant",
            "content": assistant_text or None,
            "reasoning_content": reasoning_text or None,
        }
        if tool_call_payloads:
            assistant_message["tool_calls"] = tool_call_payloads
        yield LLMStreamEvent(type=LLMEventType.STATE, provider_payload={"message": assistant_message})

    @staticmethod
    def _qwen_messages_formatter(
        messages: List[ChatMessage],
        multimodal: bool = False,
    ) -> List[Dict[str, Any]]:
        # DashScope 使用 OpenAI-compatible messages，工具结果是 role="tool" message
        result = []
        for msg in messages:
            # 只回放 Qwen 自己保存的 assistant 原生消息，其他 provider payload 只能降级为可见文本
            if msg.role == Role.ASSISTANT and msg.model_info and msg.model_info.provider_type == ProviderType.ALIBABA and msg.provider_payload:
                provider_message = dict(msg.provider_payload["message"])
                if multimodal and not isinstance(provider_message.get("content"), list):
                    provider_message["content"] = [{"text": provider_message.get("content") or ""}]
                result.append(provider_message)
                continue
            if msg.role == Role.TOOL:
                # Qwen 工具结果使用 OpenAI-compatible 的 role="tool" message
                content: Any = msg.content or ""
                if multimodal:
                    content = [{"text": msg.content or ""}]
                    content.extend({"image": f"data:{img.media_type};base64,{img.base64_data}"} for img in msg.imgs)
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id, "name": msg.tool_name, "content": content or "",
                })
                continue
            # 对于用户消息，或其他非 Qwen 提供的消息
            content: Any = msg.content or ""
            if multimodal:
                content = [{"text": msg.content or ""}]
                content.extend({"image": f"data:{img.media_type};base64,{img.base64_data}"} for img in msg.imgs)
            result.append({
                "role": msg.role.value,
                "content": content,
            })
        return result
