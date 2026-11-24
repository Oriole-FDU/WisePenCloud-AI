from __future__ import annotations

import functools
from typing import Any

from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder

# capability key -> 该能力下注册的工具名
CAPABILITY_TOOLS: dict[str, frozenset[str]] = {
    "skill_resource": frozenset({"create_skill_info", "get_skill_info", "update_skill_info"}),
    "agent_resource": frozenset({
        "create_agent_info", "get_agent_info", "get_agent_version_bundle_info",
        "update_agent_info", "update_agent_spec",
    }),
    "note_resource": frozenset({
        "apply_current_note_edits", "create_note", "read_current_note_for_edit", "read_note_resource_text",
    }),
    "resource": frozenset({
        "add_inline_comment_item", "change_group_resource_config", "change_inline_comment_resolve_status",
        "change_resource_action_permission", "change_resource_favorite_status", "create_comment", "create_comment_reply",
        "create_favorite_collection", "create_folder", "delete_comment_item", "delete_favorite_collection",
        "delete_inline_comment_item", "get_group_resource_config", "get_resource_base_info", "get_resource_favorite_status",
        "get_resource_tag_tree", "get_resource_user_interaction_record", "list_comment_replies", "list_comments",
        "list_favorite_collections", "list_favorited_resources", "list_inline_comments", "list_resources",
        "mount_resources_to_group", "move_folders", "move_resources_in_group", "move_resources_to_folder",
        "move_resources_to_trash", "remove_folders", "rename_resource", "search_resources",
        "unmount_resources_from_group", "update_favorite_collection_info", "update_folder", "update_inline_comment_item",
    }),
    "document_resource": frozenset({"get_document_source_file", "read_document_resource_text"}),
    "group": frozenset({
        "create_group", "get_group_base_info", "join_group", "list_group_members",
        "list_group_resource_daily_metrics", "list_groups",
    }),
    "user": frozenset({
        "get_my_user_info", "get_my_wallet_info", "get_unread_message_count", "list_my_messages",
        "list_my_task_status", "list_my_wallet_transactions",
    }),
}

EXTERNAL_CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_TOOLS)

CAPABILITY_LABELS: dict[str, str] = {
    "skill_resource": "技能",
    "agent_resource": "智能体",
    "note_resource": "笔记",
    "resource": "资源（文件与文件夹）",
    "document_resource": "文档",
    "group": "小组",
    "user": "个人资料、钱包与消息",
}

_TOOL_CAPABILITY: dict[str, str] = {
    tool: cap for cap, tools in CAPABILITY_TOOLS.items() for tool in tools
}


def assert_capability_consistent(registered_tool_names: set[str]) -> None:
    """确保能力映射里声明的工具名真实注册过"""
    missing = sorted(_TOOL_CAPABILITY.keys() - registered_tool_names)
    if missing: raise RuntimeError(f"capability references unknown tools: {missing}")


def apply_capability_guard(mcp) -> None:
    """包装 mcp.tool，外部调用时检查能力是否被用户授权；内部调用不受影响"""
    original_tool = mcp.tool

    def guarded_tool(name: str | None = None, **kwargs: Any):
        base_decorator = original_tool(name=name, **kwargs)

        def decorator(fn):
            capability = _TOOL_CAPABILITY.get(name) if name else None

            @functools.wraps(fn)
            async def guarded(*args: Any, **fn_kwargs: Any):
                # 不是外部流量
                if not SecurityContextHolder.is_external_mcp():
                    return await fn(*args, **fn_kwargs)
                # 未被授权
                if capability is None or capability not in SecurityContextHolder.get_mcp_capabilities():
                    raise PermissionException(PermissionErrorCode.OPERATION_UNAUTHORIZED)
                # 外部流量但已经授权
                return await fn(*args, **fn_kwargs)
            return base_decorator(guarded)

        return decorator

    mcp.tool = guarded_tool


def apply_list_tools_filter(mcp) -> None:
    """内部流量原样返回全部工具，外部流量只保留已授权的工具"""
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None:
        return
    original = tool_manager.list_tools

    def filtered() -> list:
        tools = original()
        if not SecurityContextHolder.is_external_mcp():
            return tools
        granted = SecurityContextHolder.get_mcp_capabilities()
        return [t for t in tools if _TOOL_CAPABILITY.get(getattr(t, "name", "")) in granted]

    tool_manager.list_tools = filtered


__all__ = [
    "CAPABILITY_LABELS",
    "CAPABILITY_TOOLS",
    "EXTERNAL_CAPABILITIES",
    "apply_capability_guard",
    "apply_list_tools_filter",
    "assert_capability_consistent",
]
