"""
将 MongoDB 中按 OpenAI 格式存储的 ChatMessage 列表转换为
Vercel AI SDK 6.x UIMessage 格式（带 parts 数组），供前端 useChat 的 initialMessages 使用。
"""
import json
from collections.abc import Mapping
from typing import AbstractSet, Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel

from chat.domain.entities import ChatMessage, Role


class SelectedAttachment(TypedDict):
    attachmentId: str
    filename: str
    kind: Literal["temporary", "resource"]
    available: bool


def convert_to_ui_messages(
    messages: List[ChatMessage],
    *,
    available_attachment_ids: AbstractSet[str],
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
            result.append(_build_user_ui_message(first, available_attachment_ids))
        else:
            ui_msg = _build_assistant_ui_message(group)
            if ui_msg:
                result.append(ui_msg)

    return result


def _build_user_ui_message(
    msg: ChatMessage,
    available_attachment_ids: AbstractSet[str],
) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = []
    if msg.content:
        parts.append({"type": "text", "text": msg.content, "state": "done"})
    selected_attachments = _build_selected_attachments(msg.metadata, available_attachment_ids)
    ui_message: Dict[str, Any] = {
        "id": str(msg.id) if msg.id else "",
        "role": "user",
        "parts": parts,
        "createdAt": msg.created_at.isoformat(),
    }
    if selected_attachments:
        ui_message["metadata"] = {"selectedAttachments": selected_attachments}
    return ui_message


def _build_selected_attachments(
    metadata: Mapping[str, Any],
    available_attachment_ids: AbstractSet[str],
) -> List[SelectedAttachment]:
    selected_ids = metadata.get("user_defined_attachment_ids")
    if not isinstance(selected_ids, list):
        return []

    snapshots: Dict[str, tuple[str, Literal["temporary", "resource"]]] = {}
    _collect_attachment_snapshots(snapshots, metadata.get("temp_attachments"), "temporary")
    _collect_attachment_snapshots(snapshots, metadata.get("resource_attachments"), "resource")

    result: List[SelectedAttachment] = []
    seen_ids: set[str] = set()
    for attachment_id in selected_ids:
        if not isinstance(attachment_id, str) or not attachment_id or attachment_id in seen_ids:
            continue
        snapshot = snapshots.get(attachment_id)
        if snapshot is None:
            continue
        seen_ids.add(attachment_id)
        filename, kind = snapshot
        result.append({
            "attachmentId": attachment_id,
            "filename": filename,
            "kind": kind,
            "available": attachment_id in available_attachment_ids,
        })
    return result


def _collect_attachment_snapshots(
    target: Dict[str, tuple[str, Literal["temporary", "resource"]]],
    values: Any,
    kind: Literal["temporary", "resource"],
) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        snapshot = _to_mapping(value)
        if snapshot is None:
            continue
        attachment_id = snapshot.get("attachment_id")
        filename = snapshot.get("attachment_name")
        if not isinstance(attachment_id, str) or not attachment_id:
            continue
        if not isinstance(filename, str) or not filename:
            continue
        target.setdefault(attachment_id, (filename, kind))


def _to_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return value
    return None


def _build_assistant_ui_message(group: List[ChatMessage]) -> Optional[Dict[str, Any]]:
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

                    parts.append({
                        "type": f"tool-{tc.name}",
                        "toolCallId": tc.call_id,
                        "state": "output-available",
                        "input": parsed_input,
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
    return {
        "id": last_id,
        "role": "assistant",
        "parts": parts,
        "createdAt": group[0].created_at.isoformat(),
    }
