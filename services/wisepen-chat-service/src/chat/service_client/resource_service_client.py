from __future__ import annotations

from typing import Mapping

from common.core.domain import GroupRoleType
from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient
from chat.domain.entities import ResourceItemInfo, ResourcePermission


_DEFAULT_SERVICE_NAME = "wisepen-resource-service"
_CHECK_RES_PERMISSION_PATH = "/internal/resource/checkResPermission"
_GET_RESOURCE_INFO_PATH = "/internal/resource/getResourceInfo"


class ResourceClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def check_res_permission(
        self,
        resource_id: str,
        user_id: str | int,
        group_role_map: Mapping[str, GroupRoleType],
        target_version: int | None = None,
    ) -> ResourcePermission:
        resource_id = (resource_id or "").strip()
        try:
            data = await self._rpc.post(
                self._service_name,
                _CHECK_RES_PERMISSION_PATH,
                json={
                    "resourceId": resource_id,
                    "userId": int(user_id),
                    "groupRoles": self._serialize_group_roles(group_role_map),
                    "targetVersion": target_version,
                },
            )
        except RpcError as e:
            raise e
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name, path=_CHECK_RES_PERMISSION_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return ResourcePermission.from_response(data)

    async def has_load_permission(
        self,
        resource_id: str,
        user_id: str | int,
        group_role_map: Mapping[str, GroupRoleType],
        target_version: int | None = None,
    ) -> bool:
        permission = await self.check_res_permission(
            resource_id=resource_id,
            user_id=user_id,
            group_role_map=group_role_map,
            target_version=target_version,
        )
        return bool(permission and permission.allows("LOAD"))

    async def get_resource_info(
        self,
        resource_id: str,
        user_id: str | int,
        group_role_map: Mapping[str, GroupRoleType],
        target_version: int | None = None,
    ) -> ResourceItemInfo:
        resource_id = (resource_id or "").strip()
        try:
            data = await self._rpc.post(
                self._service_name,
                _GET_RESOURCE_INFO_PATH,
                json={
                    "resourceId": resource_id,
                    "userId": int(user_id),
                    "groupRoles": self._serialize_group_roles(group_role_map),
                    "targetVersion": target_version,
                },
            )
        except RpcError as e:
            raise e
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name, path=_GET_RESOURCE_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return ResourceItemInfo.from_response(data)

    @staticmethod
    def _serialize_group_roles(group_role_map: Mapping[str, GroupRoleType]) -> dict[str, int]:
        serialized: dict[str, int] = {}
        for group_id, role in (group_role_map or {}).items():
            try:
                serialized[str(group_id)] = int(role.code)
            except (AttributeError, TypeError, ValueError):
                continue
        return serialized
