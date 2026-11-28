"""
将 MongoDB 中按 OpenAI 格式存储的 ChatMessage 列表转换为
Vercel AI SDK 6.x UIMessage 格式（带 parts 数组），供前端 useChat 的 initialMessages 使用。
"""
import json
from typing import Any, Dict, List, Optional

from chat.domain.entities import ChatMessage, Role, ToolResultStatus, SuspendedChat

def convert_to_ui_messages(
    messages: List[ChatMessage],
    suspended_chat: SuspendedChat | None = None,
    message_feedbacks: dict[str, str] | None = None,
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

    message_feedbacks = message_feedbacks or {}
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
            ui_msg = _build_assistant_ui_message(group, suspended_chat, message_feedbacks)
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
    suspended_chat: SuspendedChat | None = None,
    message_feedbacks: dict[str, str] | None = None,
) -> Optional[Dict[str, Any]]:
    """
    将一组连续的 assistant + tool 消息合并为单个 assistant UIMessage。

    遍历顺序即 DB 的 created_at 顺序，保证 parts 的排列与首次流式显示一致：
      step-start → reasoning → tool-invocations → text → step-start → ...
    """
    if not group:
        return None

    message_feedbacks = message_feedbacks or {}

    suspended_chat_tool_states = {}
    if suspended_chat is not None:
        # 如果当前对话已经挂起，前端无法从 stream 获取消息，需要额外补工具待处理状态
        for item in suspended_chat.context.turn_suspension.classified_tool_invocation_plan.approval_required_tools:
            suspended_chat_tool_states[item.tool_call_id] = "approval-requested"
        for item in suspended_chat.context.turn_suspension.classified_tool_invocation_plan.client_tools:
            suspended_chat_tool_states[item.tool_call_id] = "input-available"

    # 预构建 tool 结果查找表: tool_call_id → tool message
    tool_results: Dict[str, ChatMessage] = {}
    for msg in group:
        if msg.role == Role.TOOL and msg.tool_call_id:
            tool_results[msg.tool_call_id] = msg

    parts: List[Dict[str, Any]] = []
    last_assistant: Optional[ChatMessage] = None

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

                    tool_result: ChatMessage | None = tool_results.get(tc.call_id)
                    tool_part: Dict[str, Any] = {
                        "title": tc.display_name or tc.name,
                        "type": f"tool-{tc.name}",
                        "toolCallId": tc.call_id,
                        "input": parsed_input,
                    }
                    # 工具未返回结果，tool_state 即 input-available
                    if tool_result is None:
                        tool_part["state"] = "input-available"
                    # 工具结果状态为 ERROR，tool_state 即 output-error，追加 errorText 字段
                    elif tool_result.tool_result_status == ToolResultStatus.ERROR:
                        tool_part["state"] = "output-error"
                        tool_part["errorText"] = tool_result.tool_error_text or "Tool execution failed."
                    # 工具结果状态为 DENIED，tool_state 即 output-denied，追加 approval 字段
                    elif tool_result.tool_result_status == ToolResultStatus.DENIED:
                        tool_part["state"] = "output-denied"
                        tool_part["approval"] = {"id": tc.call_id, "approved": False}
                    # 工具结果状态为 SUCCESS，tool_state 即 output-available，追加 output 字段
                    elif tool_result.tool_result_status == ToolResultStatus.SUCCESS:
                        tool_part["state"] = "output-available"
                        tool_part["output"] = tool_result.content
                    else: # 旧版数据兜底
                        tool_part["state"] = "output-available"
                        tool_part["output"] = tool_result.content

                    # 已挂起时额外补偿状态
                    suspended_chat_tool_state = suspended_chat_tool_states.get(tc.call_id)
                    if suspended_chat_tool_state is not None:
                        tool_part["state"] = suspended_chat_tool_state
                        # approval-requested 追加 approval 字段
                        if suspended_chat_tool_state == "approval-requested":
                            tool_part["approval"] = {"id": tc.call_id}

                    parts.append(tool_part)

            if msg.content:
                parts.append({
                    "type": "text",
                    "text": msg.content,
                    "state": "done",
                })

            last_assistant = msg

    # 没有 assistant（没 parts 等同于没 assistant）
    if not parts:
        return None

    # 使用最后一条 assistant 消息的 id 作为 UIMessage id
    result: Dict[str, Any] = {
        "id": str(last_assistant.id),
        "role": "assistant",
        "parts": parts,
        "createdAt": group[0].created_at.isoformat(),
    }

    metadata: Dict[str, Any] = {}

    if last_assistant is not None:
        # 如果最后一条消息有模型信息，那么带入 meta 中
        if last_assistant.model_info is not None:
            metadata["model"] = {
                "id": last_assistant.model_info.model_id,
                "name": last_assistant.model_info.display_name or last_assistant.model_info.model_name,
                "modelName": last_assistant.model_info.model_name,
            }

        # 如果最后一条消息有 agent 信息，那么带入 meta 中
        if last_assistant.agent_info is not None:
            metadata["agent"] = {
                "id": last_assistant.agent_info.agent_id,
                "name": last_assistant.agent_info.agent_name,
            }

        # 如果最后一条消息有结束原因，那么带入 meta 中
        if last_assistant.end_reason is not None:
            metadata["endReason"] = last_assistant.end_reason.value
            if last_assistant.end_reason_detail:
                metadata["endReasonDetail"] = last_assistant.end_reason_detail

        # 消息的 feedback 落在最后一条消息上
        message_feedback = message_feedbacks.get(str(last_assistant.id))
        if message_feedback:
            metadata["feedback"] = message_feedback

    if metadata:
        result["metadata"] = metadata
    return result
