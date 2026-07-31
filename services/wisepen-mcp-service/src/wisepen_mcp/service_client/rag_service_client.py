from __future__ import annotations

from typing import Any

from common.core.exceptions import RpcError, ServiceException
from common.http.rpc_client import RpcClient

from wisepen_mcp.domain.error_codes import McpErrorCode

_DEFAULT_SERVICE_NAME = "wisepen-rag-service"
_LOCATE_PATH = "/internal/rag/knowledge-navigation/locate"
_EXPAND_PATH = "/internal/rag/knowledge-navigation/expand"
_SECTIONS_PATH = "/internal/rag/knowledge-navigation/sections"


class RagServiceClient:
    __slots__ = ("_rpc", "_service_name")

    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def locate(
        self,
        *,
        session_id: str,
        query: str,
        max_results: int,
    ) -> dict[str, Any]:
        return await self._post(
            _LOCATE_PATH,
            {
                "session_id": session_id,
                "query": query,
                "max_results": max_results,
            },
        )

    async def expand(
        self,
        *,
        session_id: str,
        state_id: str,
        node_ids: tuple[str, ...],
        query: str | None,
        relation_types: tuple[str, ...],
        direction: str,
        max_depth: int,
        max_results: int,
    ) -> dict[str, Any]:
        return await self._post(
            _EXPAND_PATH,
            {
                "session_id": session_id,
                "state_id": state_id,
                "node_ids": list(node_ids),
                "query": query,
                "relation_types": list(relation_types),
                "direction": direction,
                "max_depth": max_depth,
                "max_results": max_results,
            },
        )

    async def read_sections(
        self,
        *,
        session_id: str,
        state_id: str,
        section_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        return await self._post(
            _SECTIONS_PATH,
            {
                "session_id": session_id,
                "state_id": state_id,
                "section_ids": list(section_ids),
            },
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            data = await self._rpc.post(
                self._service_name,
                path,
                json=payload,
                timeout=300.0,
            )
        except RpcError as error:
            if error.code == McpErrorCode.RAG_NAVIGATION_INVALID.code:
                raise ServiceException(McpErrorCode.RAG_NAVIGATION_INVALID, error.msg) from error
            if error.code == McpErrorCode.RAG_NAVIGATION_STATE_NOT_FOUND.code:
                raise ServiceException(McpErrorCode.RAG_NAVIGATION_STATE_NOT_FOUND) from error
            if error.code == McpErrorCode.RAG_NAVIGATION_STATE_INVALIDATED.code:
                raise ServiceException(McpErrorCode.RAG_NAVIGATION_STATE_INVALIDATED) from error
            raise ServiceException(McpErrorCode.RAG_NAVIGATION_FAILED, error.msg) from error

        if not isinstance(data, dict):
            raise ServiceException(
                McpErrorCode.RAG_NAVIGATION_FAILED,
                f"unexpected data payload: {data!r}",
            )
        return data
