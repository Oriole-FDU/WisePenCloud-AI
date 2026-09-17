from __future__ import annotations

from typing import Any

from common.core.exceptions import RpcError, ServiceException
from common.http.rpc_client import RpcClient

from wisepen_mcp.domain.error_codes import McpErrorCode

_RAG_SERVICE_NAME = "wisepen-rag-service"


class RagServiceClient:
    """RAG HTTP 能力的 MCP 领域适配器；不把 RAG 内部模型带入 MCP。"""

    def __init__(self, rpc: RpcClient, *, service_name: str = _RAG_SERVICE_NAME) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            data = await self._rpc.post(self._service_name, path, json=payload)
        except RpcError as exc:
            if exc.code is not None and 40000 <= exc.code < 50000:
                raise ServiceException(McpErrorCode.RAG_REQUEST_INVALID, exc.msg) from exc
            raise ServiceException(McpErrorCode.RAG_SERVICE_UNAVAILABLE, exc.msg) from exc
        if not isinstance(data, dict):
            raise ServiceException(
                McpErrorCode.RAG_SERVICE_UNAVAILABLE,
                f"unexpected RAG response payload: {data!r}",
            )
        return data

    async def search_hybrid(self, *, query: str, top_k: int) -> dict[str, Any]:
        return await self._post(
            "/rag/retrieval/searchHybrid",
            {"query": query, "top_k": top_k},
        )

    async def read_pages(self, *, resource_id: str, page_labels: list[str]) -> dict[str, Any]:
        return await self._post(
            "/rag/reading/readPages",
            {"resource_id": resource_id, "page_labels": page_labels},
        )

    async def read_sections(
        self, *, section_ids: list[str], recursive: bool, max_depth: int
    ) -> dict[str, Any]:
        return await self._post(
            "/rag/reading/readSections",
            {
                "section_ids": section_ids,
                "mode": "recursive" if recursive else "direct",
                "max_depth": max_depth,
            },
        )

    async def get_neighborhood(
        self, *, section_ids: list[str], sibling_steps: int
    ) -> dict[str, Any]:
        return await self._post(
            "/rag/reading/getNeighborhood",
            {"section_ids": section_ids, "sibling_steps": sibling_steps},
        )

    async def get_global_outline(
        self, *, resource_id: str, max_level: int
    ) -> dict[str, Any]:
        return await self._post(
            "/rag/reading/getGlobalOutline",
            {"resource_id": resource_id, "max_level": max_level},
        )


__all__ = ["RagServiceClient"]
