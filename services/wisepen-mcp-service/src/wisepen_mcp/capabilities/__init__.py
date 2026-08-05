from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from wisepen_mcp.capabilities.skill_creator import register_skill_creator_tools
from wisepen_mcp.capabilities.rag import register_rag_tools
from wisepen_mcp.capabilities.web_search import (
    WebSearchService,
    register_web_search_tools,
)
from wisepen_mcp.service_client import AIAssetClient, RagServiceClient


def build_mcp_server(
    *,
    ai_asset_client: AIAssetClient,
    rag_service_client: RagServiceClient,
    web_search_service: WebSearchService,
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
    register_skill_creator_tools(mcp, ai_asset_client)
    register_rag_tools(mcp, rag_service_client)
    register_web_search_tools(mcp, web_search_service)
    return mcp


__all__ = ["build_mcp_server"]

