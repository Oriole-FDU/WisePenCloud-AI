from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from httpx import AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor
from common.cloud.service_discovery import LoadBalancingStrategy, ServiceDiscovery
from common.core.constants import CommonConstants, SecurityConstants
from common.core.exceptions import RpcError
from common.gray.context import GrayContextHolder
from common.mcp import TOOL_CONFIG_META_KEY, TOOL_CONTEXT_META_KEY, ToolMeta
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
        mcp_path: str = _MCP_PATH,
        timeout: float = 30.0,
        default_strategy: Optional[LoadBalancingStrategy] = None,
    ) -> None:
        self._discovery = discovery
        self._from_source_secret = from_source_secret
        self._service_name = service_name
        self._mcp_path = mcp_path
        self._timeout = timeout
        self._strategy = default_strategy

    async def list_tools(self) -> list[McpToolDescriptor]:
        async with AsyncClient(
            headers=self._build_headers(),
            timeout=self._timeout,
        ) as http_client:
            async with streamable_http_client(
                url=await self._resolve_url(),
                http_client=http_client,
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

    async def list_tool_metas(self) -> list[ToolMeta]:
        async with AsyncClient(
            headers=self._build_headers(),
            timeout=self._timeout,
        ) as http_client:
            response = await http_client.get(f"{await self._resolve_base_url()}/internal/tools/meta")

        body = response.json()
        if not isinstance(body, dict) or int(body.get("code")) != 200:
            raise RpcError(
                service_name=self._service_name,
                path="/internal/tools/meta",
                status=response.status_code,
                msg=f"unexpected meta payload: {body!r}",
            )
        data = body.get("data")
        if not isinstance(data, list):
            raise RpcError(
                service_name=self._service_name,
                path="/internal/tools/meta",
                msg=f"unexpected meta data: {data!r}",
            )
        return [ToolMeta.model_validate(item) for item in data]

    async def call_tool(
        self,
        server: Any,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        tool_config: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> CallToolResult:
        meta: dict[str, Any] = {}
        if tool_config:
            meta[TOOL_CONFIG_META_KEY] = dict(tool_config)
        if tool_context:
            meta[TOOL_CONTEXT_META_KEY] = dict(tool_context)

        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        async with AsyncClient(
            headers=self._build_headers(),
            timeout=timeout,
        ) as http_client:
            async with streamable_http_client(
                url=await self._resolve_url(),
                http_client=http_client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await session.call_tool(
                        tool_name,
                        dict(arguments),
                        meta=meta or None,
                    )

    async def _resolve_url(self) -> str:
        return f"{await self._resolve_base_url()}{self._mcp_path}"

    async def _resolve_base_url(self) -> str:
        instance = await self._discovery.pick(self._service_name, strategy=self._strategy)
        return f"http://{instance.ip}:{instance.port}"

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}

        headers[SecurityConstants.HEADER_FROM_SOURCE] = self._from_source_secret
        user_id = SecurityContextHolder.get_user_id()
        identity_type = SecurityContextHolder.get_identity_type()
        user_status = SecurityContextHolder.get_user_status()
        if user_id:
            headers[SecurityConstants.HEADER_USER_ID] = user_id
            headers[SecurityConstants.HEADER_IDENTITY_TYPE] = str(identity_type.code)
            headers[SecurityConstants.HEADER_USER_STATUS] = str(user_status)
            headers[SecurityConstants.HEADER_GROUP_ROLE_MAP] = json.dumps({
                str(group_id): role.code
                for group_id, role in SecurityContextHolder.get_group_role_map().items()
            }, ensure_ascii=False)
        developer = GrayContextHolder.get_developer_tag()
        if developer:
            headers[CommonConstants.GRAY_HEADER_DEV_KEY] = developer
        return headers
