from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from httpx import AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor
from common.cloud.service_discovery import LoadBalancingStrategy, ServiceDiscovery
from common.core.constants import CommonConstants, SecurityConstants
from common.gray.context import GrayContextHolder
from common.security.context import SecurityContextHolder


_DEFAULT_SERVICE_NAME = "wisepen-mcp-service"
_MCP_PATH = "/mcp/"


class McpServiceClient:
    def __init__(
        self,
        discovery: ServiceDiscovery,
        *,
        from_source_secret: str,
        service_name: str = _DEFAULT_SERVICE_NAME,
        timeout: float = 30.0,
        default_strategy: Optional[LoadBalancingStrategy] = None,
        base_url: str = "",
    ) -> None:
        self._discovery = discovery
        self._from_source_secret = from_source_secret
        self._service_name = service_name
        self._timeout = timeout
        self._strategy = default_strategy
        self._base_url = base_url.rstrip("/")

    async def list_tools(self) -> list[McpToolDescriptor]:
        async with streamable_http_client(
            url=await self._resolve_url(),
            http_client=AsyncClient(
                headers=self._build_headers(),
                timeout=self._timeout,
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
            if not name or not description:
                continue
            input_schema = item.inputSchema
            if hasattr(input_schema, "model_dump"):
                input_schema = input_schema.model_dump(by_alias=True)
            descriptors.append(McpToolDescriptor(name=name, description=description, input_schema=input_schema))
        return descriptors

    async def call_tool(
        self,
        server: Any,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> str:
        request_timeout = timeout_seconds or self._timeout
        async with streamable_http_client(
            url=await self._resolve_url(),
            http_client=AsyncClient(
                headers=self._build_headers(context),
                timeout=request_timeout,
            ),
            terminate_on_close=True,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, dict(arguments))

        output = json.dumps(result.structuredContent, ensure_ascii=False, default=str)
        if getattr(result, "isError", False):
            raise RuntimeError(output or f"MCP tool '{tool_name}' returned an error.")
        return output

    async def _resolve_url(self) -> str:
        try:
            instance = await self._discovery.pick(self._service_name, strategy=self._strategy)
            return f"http://{instance.ip}:{instance.port}{_MCP_PATH}"
        except Exception:
            if self._base_url:
                return f"{self._base_url}{_MCP_PATH}"
            raise

    def _build_headers(self, context: Mapping[str, Any] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        context = context or {}

        headers[SecurityConstants.HEADER_FROM_SOURCE] = self._from_source_secret
        user_id = str(context.get("user_id") or SecurityContextHolder.get_user_id() or "").strip()
        identity_type = SecurityContextHolder.get_identity_type()
        if user_id:
            headers[SecurityConstants.HEADER_USER_ID] = user_id
            if identity_type is not None:
                headers[SecurityConstants.HEADER_IDENTITY_TYPE] = str(identity_type.code)
            headers[SecurityConstants.HEADER_GROUP_ROLE_MAP] = json.dumps({
                str(group_id): role.code
                for group_id, role in SecurityContextHolder.get_group_role_map().items()
            }, ensure_ascii=False)
        session_id = str(context.get("session_id") or SecurityContextHolder.get_session_id() or "").strip()
        if session_id:
            headers[SecurityConstants.HEADER_SESSION_ID] = session_id
        request_id = str(context.get("request_id") or "").strip()
        if request_id:
            headers[SecurityConstants.HEADER_REQUEST_ID] = request_id
        developer = GrayContextHolder.get_developer_tag()
        if developer:
            headers[CommonConstants.GRAY_HEADER_DEV_KEY] = developer
        return headers
