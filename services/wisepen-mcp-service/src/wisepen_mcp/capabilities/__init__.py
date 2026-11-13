__all__ = []
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from wisepen_mcp.capabilities.agent_resource import register_agent_resource_tools
from wisepen_mcp.capabilities.resource import register_resource_tools
from wisepen_mcp.capabilities.document_resource import register_document_resource_tools
from wisepen_mcp.capabilities.group import register_group_tools
from wisepen_mcp.capabilities.note_resource import register_note_resource_tools
from wisepen_mcp.capabilities.skill_resource import register_skill_resource_tools
from wisepen_mcp.capabilities.user import register_user_tools
from wisepen_mcp.capabilities.web_search import register_web_search_tools
from wisepen_mcp.service_client import AIAssetClient, DocumentClient, NoteClient, NoteCollabClient, ResourceClient, UserClient


def build_mcp_server(
    *,
    ai_asset_client: AIAssetClient,
    note_collab_client: NoteCollabClient,
    resource_client: ResourceClient,
    document_client: DocumentClient,
    note_client: NoteClient,
    user_client: UserClient,
) -> FastMCP:
    mcp = FastMCP(
        "wisepen-mcp-service",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    register_skill_resource_tools(mcp, ai_asset_client)
    register_agent_resource_tools(mcp, ai_asset_client)
    register_note_resource_tools(mcp, note_collab_client, resource_client, note_client)
    register_resource_tools(mcp, resource_client)
    register_document_resource_tools(mcp, resource_client, document_client)
    register_group_tools(mcp, user_client)
    register_user_tools(mcp, user_client)
    register_web_search_tools(mcp)
    return mcp


__all__ = ["build_mcp_server"]

