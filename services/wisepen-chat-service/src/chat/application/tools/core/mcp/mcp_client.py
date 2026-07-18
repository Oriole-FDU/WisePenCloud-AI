from __future__ import annotations

import json
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse
from dataclasses import dataclass, field
from typing import Any, Mapping

from httpx import AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult


from chat.core.config.app_settings import settings
from chat.domain.entities import UserMcpServerConfig
from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException

_MCP_PATH = "/mcp/"


@dataclass
class McpServerConnection:
    server_id: str
    url: str | None = None
    path: str = _MCP_PATH
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None

    def __init__(self, config: UserMcpServerConfig) -> None:
        self.server_id = config.server_id
        self.url = config.url
        self.headers = {
            **dict(config.headers or {}),
            **dict(config.secret_headers or {}),
        }
        self.timeout_seconds = settings.MCP_DEFAULT_TIMEOUT_SECONDS


class McpClient:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._timeout = timeout

    async def list_tools(self, server: McpServerConnection) -> list[McpToolDescriptor]:
        url = await self._resolve_url(server)
        async with streamable_http_client(
            url,
            http_client=AsyncClient(
                headers=server.headers,
                timeout=server.timeout_seconds or self._timeout,
            ),
            terminate_on_close=True,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()

        descriptors: list[McpToolDescriptor] = []
        for item in result.tools or []:
            name = item.name.strip()
            description = item.description
            if not name or not description: continue
            input_schema = item.inputSchema
            if hasattr(input_schema, "model_dump"):
                input_schema = input_schema.model_dump(by_alias=True)
            descriptors.append(McpToolDescriptor(name=name, description=description, input_schema=input_schema))
        return descriptors

    async def call_tool(
        self,
        server: McpServerConnection,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> str:
        url = await self._resolve_url(server)

        async with streamable_http_client(
            url,
            http_client=AsyncClient(
                headers=server.headers,
                timeout=server.timeout_seconds or self._timeout,
            ),
            terminate_on_close=True,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, dict(arguments))

        output = _stringify_tool_result(result)
        if getattr(result, "isError", False):
            raise RuntimeError(output or f"MCP tool '{tool_name}' returned an error.")
        return output

    async def _resolve_url(self, server: McpServerConnection) -> str:
        parsed_url = urlparse((server.url or "").strip())
        if not parsed_url:
            raise RuntimeError(f"user MCP server '{server.server_id}' must provide a URL.")

        if parsed_url.scheme not in {"http", "https"}:
            raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_URL_INVALID, "MCP server URL must use http or https.")
        if not parsed_url.hostname:
            raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_URL_INVALID, "MCP server URL must include a hostname.")
        if parsed_url.username or parsed_url.password:
            raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_URL_INVALID, "MCP server URL must not include credentials.")

        ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
        try:
            ip = ipaddress.ip_address(parsed_url.hostname)
        except ValueError:
            ip = None

        if ip is not None:
            if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                    or str(ip) == "169.254.169.254"
            ):
                raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_URL_INVALID, f"MCP server resolves to an unsafe address: {ip}")
            return server.url.strip()

        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, parsed_url.hostname, parsed_url.port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_URL_INVALID, f"MCP server hostname cannot be resolved: {parsed_url.hostname}") from e

        if not infos:
            raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_URL_INVALID, f"MCP server hostname cannot be resolved: {parsed_url.hostname}")

        for info in infos:
            sockaddr = info[4]
            if not sockaddr:
                continue
            ip = ipaddress.ip_address(sockaddr[0])
            if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                    or str(ip) == "169.254.169.254"
            ):
                raise ServiceException(ChatErrorCode.MCP_TOOL_SERVER_URL_INVALID, f"MCP server resolves to an unsafe address: {ip}")

        return server.url.strip()


def _stringify_tool_result(result: CallToolResult) -> str:
    parts: list[str] = []
    for item in result.content:
        if item.text is not None:
            parts.append(str(item.text))
        else:
            parts.append(str(item))
    if parts:
        return "\n".join(parts)

    if result.structuredContent is not None:
        return json.dumps(result.structuredContent, ensure_ascii=False, default=str)
    return ""
