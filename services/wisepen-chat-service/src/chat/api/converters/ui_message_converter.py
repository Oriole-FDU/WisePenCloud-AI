"""
将 MongoDB 中按 OpenAI 格式存储的 ChatMessage 列表转换为
Vercel AI SDK 6.x UIMessage 格式（带 parts 数组），供前端 useChat 的 initialMessages 使用。
"""
import json
from typing import Any, Dict, List, Optional

from chat.domain.entities import ChatMessage, Role


def convert_to_ui_messages(
    messages: List[ChatMessage],
    tool_states: dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    """
    将按 created_at 排序的 ChatMessage[] 分组并转换为 UIMessage[]。

    分组规则：
    - 每条 user 消息独立成一个 UIMessage
    - user 消息之后、下一条 user 消息之前的所有 assistant + tool 消息
      合并为一个 assistant UIMessage，其 parts 按原始顺序构建
    """
    if not messages:
        return []
    
    tool_states = tool_states or {}
    groups: List[List[ChatMessage]] = []
    current_group: List[ChatMessage] = []

    for msg in messages:
        if msg.role == Role.USER:
            if current_group:
                groups.append(current_group)
            groups.append([msg])
            current_group = []
        else:
            current_group.append(msg)

    if current_group:
        groups.append(current_group)

    result: List[Dict[str, Any]] = []
    for group in groups:
        first = group[0]
        if first.role == Role.USER:
            result.append(_build_user_ui_message(first))
        else:
            ui_msg = _build_assistant_ui_message(group, tool_states)
            if ui_msg:
                result.append(ui_msg)

    return result


def _build_user_ui_message(msg: ChatMessage) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = []
    if msg.content:
        parts.append({"type": "text", "text": msg.content, "state": "done"})
    metadata = dict(msg.metadata or {})
    if msg.attachments:
        metadata["attachments"] = [
            {
                "attachmentId": attachment.attachment_id,
                "filename": attachment.name,
                "kind": attachment.kind,
                "available": True,
            }
            for attachment in msg.attachments
        ]
    result = {
        "id": str(msg.id) if msg.id else "",
        "role": "user",
        "parts": parts,
        "createdAt": msg.created_at.isoformat(),
    }
    if metadata:
        result["metadata"] = metadata
    return result


def _build_assistant_ui_message(
    group: List[ChatMessage],
    tool_states: dict[str, str],
) -> Optional[Dict[str, Any]]:
    """
    将一组连续的 assistant + tool 消息合并为单个 assistant UIMessage。

    遍历顺序即 DB 的 created_at 顺序，保证 parts 的排列与首次流式显示一致：
      step-start → reasoning → tool-invocations → text → step-start → ...
    """
    if not group:
        return None

    # 预构建 tool 结果查找表: tool_call_id → content
    tool_results: Dict[str, str] = {}
    for msg in group:
        if msg.role == Role.TOOL and msg.tool_call_id:
            tool_results[msg.tool_call_id] = msg.content or ""

    parts: List[Dict[str, Any]] = []
    last_id = ""

    for msg in group:
        if msg.role == Role.TOOL:
            continue

        if msg.role == Role.ASSISTANT:
            parts.append({"type": "step-start"})

            if msg.reasoning_content:
                parts.append({
                    "type": "reasoning",
                    "text": msg.reasoning_content,
                    "state": "done",
                })

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        parsed_input = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    except (json.JSONDecodeError, TypeError):
                        parsed_input = {}

                    tool_output = tool_results.get(tc.call_id, "")
                    tool_state = tool_states.get(tc.call_id, "output-available")
                    parts.append({
                        "type": f"tool-{tc.name}",
                        "toolCallId": tc.call_id,
                        "input": parsed_input,
                        "state": tool_state,
                        "output": tool_output,
                    })

            if msg.content:
                parts.append({
                    "type": "text",
                    "text": msg.content,
                    "state": "done",
                })

            last_id = str(msg.id) if msg.id else last_id

    if not parts:
        return None

    # 使用最后一条 assistant 消息的 id 作为 UIMessage id
    result = {
        "id": last_id,
        "role": "assistant",
        "parts": parts,
        "createdAt": group[0].created_at.isoformat(),
    }
    root = next((msg.metadata.get("root_execution_id") for msg in group
                 if msg.metadata.get("root_execution_id")), None)
    if root:
        result["metadata"] = {"root_execution_id": root}
    return result
