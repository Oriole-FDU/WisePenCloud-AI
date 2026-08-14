from __future__ import annotations

from typing import Any, Mapping

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_RESOURCE_SERVICE_NAME = "wisepen-resource-service"
_GLOBAL_SEARCH_RESOURCES_PATH = "/resource/search/globalSearchResources"
_GET_RESOURCE_INFO_PATH = "/internal/resource/getResourceInfo"


class ResourceClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _RESOURCE_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def search_user_resources(
        self,
        *,
        keyword: str,
        resource_types: list[str],
        page: int,
        size: int,
    ) -> dict[str, Any]:
        data = await self._rpc.get(
            self._service_name,
            _GLOBAL_SEARCH_RESOURCES_PATH,
            params={
                "keyword": keyword,
                "scope": "ALL",
                "resourceTypes": resource_types,
                "page": page,
                "size": size,
            },
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GLOBAL_SEARCH_RESOURCES_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data

    async def get_resource_info(
        self,
        *,
        resource_id: str,
        user_id: str | int,
        group_roles: Mapping[str, int],
    ) -> dict[str, Any]:
        data = await self._rpc.post(
            self._service_name,
            _GET_RESOURCE_INFO_PATH,
            json={
                "resourceId": resource_id,
                "userId": int(user_id),
                "groupRoles": {str(group_id): int(role) for group_id, role in group_roles.items()},
            },
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_RESOURCE_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data

__all__ = ["ResourceClient"]
