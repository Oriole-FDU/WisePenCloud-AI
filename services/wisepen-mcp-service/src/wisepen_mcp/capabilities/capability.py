from __future__ import annotations

import functools
from typing import Any

from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from wisepen_mcp.capabilities.tool_category import CATEGORY_TOOLS, tool_category


CAPABILITY_LABELS: dict[str, str] = {
    "skill_resource": "技能",
    "agent_resource": "智能体",
    "note_resource": "笔记",
    "resource_file": "资源文件",
    "resource_comment": "资源评论与批注",
    "resource_favorite": "资源收藏",
    "resource_permission": "资源权限与组配置",
    "document_resource": "文档",
    "group": "小组",
    "user": "个人资料、钱包与消息",
}

EXTERNAL_CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_LABELS)

def tool_capability(tool_name: str) -> str | None:
    """返回外部 MCP 调用所需的授权能力"""
    category = tool_category(tool_name)
    return category if category in EXTERNAL_CAPABILITIES else None


def apply_capability_guard(mcp) -> None:
    """包装 mcp.tool，外部调用时检查能力是否被用户授权；内部调用不受影响"""
    original_tool = mcp.tool

    def guarded_tool(name: str | None = None, **kwargs: Any):
        base_decorator = original_tool(name=name, **kwargs)

        def decorator(fn):
            capability = tool_capability(name) if name else None

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
    """内部流量返回全部工具，外部流量只返回已授权工具"""
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None:
        return
    original = tool_manager.list_tools

    def filtered() -> list:
        tools = original()
        if not SecurityContextHolder.is_external_mcp():
            return tools
        granted = SecurityContextHolder.get_mcp_capabilities()
        return [tool for tool in tools if tool_capability(getattr(tool, "name", "")) in granted]

    tool_manager.list_tools = filtered


__all__ = [
    "CAPABILITY_LABELS",
    "EXTERNAL_CAPABILITIES",
    "apply_capability_guard",
    "apply_list_tools_filter",
    "tool_capability",
]
