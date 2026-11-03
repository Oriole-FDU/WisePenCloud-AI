from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import litellm

from chat.domain.entities.message import ChatMessage, Role
from chat.domain.entities.token_usage import TokenUsageSource

if TYPE_CHECKING:
    from chat.domain.interfaces.llm import LLMStreamEvent
    from chat.domain.repositories.model_repo import ModelRequestInfo


async def count_message_tokens(
    messages: List[ChatMessage],
    model_name: str = "gpt-4o",
    tools: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """估算消息及工具定义的 token 数，计数失败时按原有策略兜底。"""
    payload = _convert_messages(messages)
    try:
        return await asyncio.to_thread(litellm.token_counter, model=model_name, messages=payload, tools=tools)
    except Exception:
        if tools:
            payload.append({"tools": tools})
        return max(1, len(json.dumps(payload, ensure_ascii=False, default=str)))


def _convert_messages(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    formatted_messages = []
    for message in messages:
        if message.role == Role.ASSISTANT:
            payload = {"role": message.role.value, "content": message.content, "reasoning": message.reasoning_content}
            if message.tool_calls:
                payload["tool_calls"] = []
                for tool_call in message.tool_calls:
                    payload['tool_calls'].append({
                        "id": tool_call.call_id,
                        "type": "function",
                        "function": {"name": tool_call.name, "arguments": tool_call.arguments},
                    })
        else:
            payload = {"role": message.role.value, "content": message.content}
            if message.role == Role.TOOL:
                if message.tool_call_id:
                    payload["tool_call_id"] = message.tool_call_id
                if message.tool_name:
                    payload["name"] = message.tool_name

        formatted_messages.append(payload)
    return formatted_messages


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    usage_source: TokenUsageSource = TokenUsageSource.PROVIDER

    def __post_init__(self) -> None:
        self.usage_source = TokenUsageSource(self.usage_source)
        self.input_tokens = max(int(self.input_tokens or 0), 0)
        self.cached_input_tokens = min(max(int(self.cached_input_tokens or 0), 0), self.input_tokens)
        self.output_tokens = max(int(self.output_tokens or 0), 0)

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "TokenUsage") -> None:
        if other is None:
            return
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.cached_input_tokens = min(self.cached_input_tokens, self.input_tokens)

        if self.total_tokens == 0: self.usage_source = other.usage_source
        elif self.usage_source != other.usage_source: self.usage_source = TokenUsageSource.MIXED

    def collect_stream_usage(self, event: "LLMStreamEvent") -> None:
        """原地累加流中的用量，忽略其他事件及缺失的用量。"""
        # 流事件持有 TokenUsage，按需导入事件类型以避免循环引用。
        from chat.domain.interfaces.llm import LLMEventType

        if event.type == LLMEventType.USAGE and event.usage:
            self.add(event.usage)

    async def resolve(
        self,
        *,
        messages: List[ChatMessage],
        assistant_msg: ChatMessage,
        model_info: ModelRequestInfo,
        tools: Optional[List[dict]],
    ) -> None:
        """用量完整时保持原样，否则完成全部估算后原地补全，不修改消息。"""
        if self.input_tokens == 0 and self.total_tokens > 0:
            estimated_input_tokens = await count_message_tokens(
                messages=messages,
                model_name=model_info.model_name,
                tools=tools,
            )
            estimated_output_token = self.total_tokens - estimated_input_tokens

            resolved_usage = TokenUsage(
                input_tokens=estimated_input_tokens,
                cached_input_tokens=0,
                output_tokens=max(estimated_output_token, 0),
                usage_source=TokenUsageSource.ESTIMATED_SPLIT,
            )

        elif self.total_tokens <= 0:
            estimated_input_tokens = await count_message_tokens(
                messages=messages,
                model_name=model_info.model_name,
                tools=tools,
            )
            estimated_output_token = await count_message_tokens(
                messages=[assistant_msg],
                model_name=model_info.model_name,
            )

            resolved_usage = TokenUsage(
                input_tokens=estimated_input_tokens,
                cached_input_tokens=0,
                output_tokens=estimated_output_token,
                usage_source=TokenUsageSource.ESTIMATED,
            )

        else:
            return

        # 全部估算和规范化成功后再更新，避免异常或取消留下半更新状态。
        self.input_tokens = resolved_usage.input_tokens
        self.cached_input_tokens = resolved_usage.cached_input_tokens
        self.output_tokens = resolved_usage.output_tokens
        self.usage_source = resolved_usage.usage_source

    def copy(self) -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            usage_source=self.usage_source,
        )
