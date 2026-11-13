from __future__ import annotations

from typing import Annotated, Any

from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import TagTreeNode
from wisepen_mcp.service_client import ResourceClient


def _tag_node_to_dict(node: TagTreeNode) -> dict[str, Any]:
    return {
        "tag_id": node.tag_id,
        "parent_id": node.parent_id,
        "tag_name": node.tag_name,
        "is_path": node.is_path,
        "tag_desc": node.tag_desc,
        "tag_creator": node.tag_creator,
        "creator_info": node.creator_info,
        "granted_actions": node.granted_actions,
        "children": [_tag_node_to_dict(child) for child in node.children],
    }


def register_get_resource_tag_tree_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="get_resource_tag_tree",
        description=(
            "Get the full folder/tag tree of a Wisepen resource space. Leave group_id empty for the current user's "
            "personal space, or pass a group_id (from list_groups) for a group space. The returned tag_id values are "
            "used by list_resources, create_folder, rename_folder, move_folders, and the resource placement tools. "
            "Folder nodes have is_path=true."
        ),
    )
    async def get_resource_tag_tree(
        group_id: Annotated[
            str | None,
            Field(description="Group id from list_groups; omit or pass empty for the personal space."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_group_id = group_id.strip() if group_id and group_id.strip() else None
        data = await resource_client.get_tag_tree(group_id=resolved_group_id)
        items = [_tag_node_to_dict(node) for node in data]
        return {"group_id": resolved_group_id, "items": items}


__all__ = ["register_get_resource_tag_tree_tool"]
