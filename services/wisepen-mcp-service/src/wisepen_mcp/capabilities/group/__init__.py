from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.group.create_group import register_create_group_tool
from wisepen_mcp.capabilities.group.get_group_base_info import register_get_group_base_info_tool
from wisepen_mcp.capabilities.group.join_group import register_join_group_tool
from wisepen_mcp.capabilities.group.list_group_members import register_list_group_members_tool
from wisepen_mcp.capabilities.group.list_group_resource_daily_metrics import register_list_group_resource_daily_metrics_tool
from wisepen_mcp.capabilities.group.list_groups import register_list_groups_tool
from wisepen_mcp.service_client import UserClient


def register_group_tools(mcp: FastMCP, user_client: UserClient) -> None:
    register_list_groups_tool(mcp, user_client)
    register_list_group_members_tool(mcp, user_client)
    register_get_group_base_info_tool(mcp, user_client)
    register_create_group_tool(mcp, user_client)
    register_join_group_tool(mcp, user_client)
    register_list_group_resource_daily_metrics_tool(mcp, user_client)


__all__ = ["register_group_tools"]
