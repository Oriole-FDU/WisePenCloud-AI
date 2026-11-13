from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.resource.create_folder import register_create_folder_tool
from wisepen_mcp.capabilities.resource.get_resource_base_info import register_get_resource_base_info_tool
from wisepen_mcp.capabilities.resource.get_resource_tag_tree import register_get_resource_tag_tree_tool
from wisepen_mcp.capabilities.resource.list_resources import register_list_resources_tool
from wisepen_mcp.capabilities.resource.mount_resources_to_group import register_mount_resources_to_group_tool
from wisepen_mcp.capabilities.resource.move_folders import register_move_folders_tool
from wisepen_mcp.capabilities.resource.move_resources_in_group import register_move_resources_in_group_tool
from wisepen_mcp.capabilities.resource.move_resources_to_folder import register_move_resources_to_folder_tool
from wisepen_mcp.capabilities.resource.move_resources_to_trash import register_move_resources_to_trash_tool
from wisepen_mcp.capabilities.resource.rename_folder import register_rename_folder_tool
from wisepen_mcp.capabilities.resource.rename_resource import register_rename_resource_tool
from wisepen_mcp.capabilities.resource.search_resources import register_search_resources_tool
from wisepen_mcp.capabilities.resource.unmount_resources_from_group import register_unmount_resources_from_group_tool
from wisepen_mcp.service_client import ResourceClient


def register_resource_tools(
    mcp: FastMCP,
    resource_client: ResourceClient,
) -> None:
    register_search_resources_tool(mcp, resource_client)
    register_get_resource_tag_tree_tool(mcp, resource_client)
    register_list_resources_tool(mcp, resource_client)
    register_get_resource_base_info_tool(mcp, resource_client)
    register_create_folder_tool(mcp, resource_client)
    register_rename_folder_tool(mcp, resource_client)
    register_move_folders_tool(mcp, resource_client)
    register_rename_resource_tool(mcp, resource_client)
    register_move_resources_to_folder_tool(mcp, resource_client)
    register_mount_resources_to_group_tool(mcp, resource_client)
    register_move_resources_in_group_tool(mcp, resource_client)
    register_unmount_resources_from_group_tool(mcp, resource_client)
    register_move_resources_to_trash_tool(mcp, resource_client)


__all__ = ["register_resource_tools"]

