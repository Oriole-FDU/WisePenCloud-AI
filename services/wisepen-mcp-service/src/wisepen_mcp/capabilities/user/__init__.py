from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.user.get_my_user_info import register_get_my_user_info_tool
from wisepen_mcp.capabilities.user.get_my_wallet_info import register_get_my_wallet_info_tool
from wisepen_mcp.capabilities.user.get_unread_message_count import register_get_unread_message_count_tool
from wisepen_mcp.capabilities.user.list_my_messages import register_list_my_messages_tool
from wisepen_mcp.capabilities.user.list_my_task_status import register_list_my_task_status_tool
from wisepen_mcp.capabilities.user.list_my_wallet_transactions import register_list_my_wallet_transactions_tool
from wisepen_mcp.service_client import UserClient


def register_user_tools(mcp: FastMCP, user_client: UserClient) -> None:
    register_get_my_user_info_tool(mcp, user_client)
    register_get_my_wallet_info_tool(mcp, user_client)
    register_list_my_wallet_transactions_tool(mcp, user_client)
    register_list_my_messages_tool(mcp, user_client)
    register_get_unread_message_count_tool(mcp, user_client)
    register_list_my_task_status_tool(mcp, user_client)


__all__ = ["register_user_tools"]
