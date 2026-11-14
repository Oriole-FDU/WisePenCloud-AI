from __future__ import annotations

from typing import Any, Mapping

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient
from wisepen_mcp.domain.entities import (
    MountResourcesToGroupRequest,
    MovePersonalResourcesToTrashRequest,
    MoveResourcesInGroupRequest,
    PageResult,
    ResourceBaseInfo,
    ResourceItem,
    ResourcePlacementResult,
    ResourceRenameRequest,
    SearchHitItem,
    SetPersonalResourcesPathTagRequest,
    TagCreateRequest,
    TagMoveRequest,
    TagTreeNode,
    TagUpdateRequest,
    UnmountResourcesFromGroupRequest,
)


_RESOURCE_SERVICE_NAME = "wisepen-resource-service"
_GLOBAL_SEARCH_RESOURCES_PATH = "/resource/search/globalSearchResources"
_GET_RESOURCE_INFO_PATH = "/internal/resource/getResourceInfo"
_GET_TAG_TREE_PATH = "/resource/tag/getTagTree"
_ADD_TAG_PATH = "/resource/tag/addTag"
_CHANGE_TAG_PATH = "/resource/tag/changeTag"
_MOVE_TAGS_PATH = "/resource/tag/moveTags"
_LIST_RESOURCES_PATH = "/resource/item/listResources"
_GET_RESOURCE_BASE_INFO_PATH = "/resource/item/getResourceBaseInfo"
_RENAME_RESOURCE_PATH = "/resource/item/renameResource"
_SET_PERSONAL_RESOURCES_PATH_TAG_PATH = "/resource/placement/setPersonalResourcesPathTag"
_MOUNT_RESOURCES_TO_GROUP_PATH = "/resource/placement/mountResourcesToGroup"
_MOVE_RESOURCES_IN_GROUP_PATH = "/resource/placement/moveResourcesInGroup"
_UNMOUNT_RESOURCES_TO_GROUP_PATH = "/resource/placement/unmountResourcesToGroup"
_MOVE_PERSONAL_RESOURCES_TO_TRASH_PATH = "/resource/placement/movePersonalResourcesToTrash"


class ResourceClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _RESOURCE_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def search_resources(
        self,
        *,
        keyword: str,
        resource_types: list[str],
        page: int,
        size: int,
    ) -> PageResult[SearchHitItem]:
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
        return PageResult[SearchHitItem].model_validate(data)

    async def get_resource_info(
        self,
        *,
        resource_id: str,
        user_id: str | int,
        group_roles: Mapping[str, int],
    ) -> ResourceItem:
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
        return ResourceItem.model_validate(data)

    async def get_tag_tree(self, *, group_id: str | None) -> list[TagTreeNode]:
        params: dict[str, Any] = {}
        if group_id is not None:
            params["groupId"] = group_id
        data = await self._rpc.get(self._service_name, _GET_TAG_TREE_PATH, params=params)
        if not isinstance(data, list):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_TAG_TREE_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return [TagTreeNode.model_validate(item) for item in data]

    async def list_resources(
        self,
        *,
        group_id: str | None,
        tag_ids: list[str] | None,
        tag_query_logic_mode: str,
        include_my_interaction: bool,
        resource_type: str | None,
        page: int,
        size: int,
        sort_by: str,
        sort_dir: str,
    ) -> PageResult[ResourceItem]:
        params: dict[str, Any] = {
            "tagQueryLogicMode": tag_query_logic_mode,
            "includeMyInteraction": include_my_interaction,
            "page": page,
            "size": size,
            "sortBy": sort_by,
            "sortDir": sort_dir,
        }
        if group_id is not None:
            params["groupId"] = group_id
        if tag_ids:
            params["tagIds"] = tag_ids
        if resource_type:
            params["resourceType"] = resource_type
        data = await self._rpc.get(self._service_name, _LIST_RESOURCES_PATH, params=params)
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_LIST_RESOURCES_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return PageResult[ResourceItem].model_validate(data)

    async def get_resource_base_info(self, *, resource_id: str) -> ResourceBaseInfo:
        data = await self._rpc.get(
            self._service_name,
            _GET_RESOURCE_BASE_INFO_PATH,
            params={"resourceId": resource_id},
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_RESOURCE_BASE_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return ResourceBaseInfo.model_validate(data)

    async def create_tag(self, *, request: TagCreateRequest) -> str:
        data = await self._rpc.post(
            self._service_name,
            _ADD_TAG_PATH,
            json=request.model_dump(by_alias=True, exclude_none=True),
        )
        if not isinstance(data, str) or not data.strip():
            raise RpcError(
                service_name=self._service_name,
                path=_ADD_TAG_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return data.strip()

    async def update_tag(self, *, request: TagUpdateRequest) -> None:
        await self._rpc.post(
            self._service_name,
            _CHANGE_TAG_PATH,
            json=request.model_dump(by_alias=True, exclude_none=True),
        )

    async def move_tags(self, *, request: TagMoveRequest) -> None:
        await self._rpc.post(
            self._service_name,
            _MOVE_TAGS_PATH,
            json=request.model_dump(by_alias=True, exclude_none=True),
        )

    async def rename_resource(self, *, request: ResourceRenameRequest) -> None:
        await self._rpc.post(
            self._service_name,
            _RENAME_RESOURCE_PATH,
            json=request.model_dump(by_alias=True),
        )

    async def set_personal_resources_path_tag(
        self,
        *,
        request: SetPersonalResourcesPathTagRequest,
    ) -> ResourcePlacementResult:
        return await self._post_placement(
            _SET_PERSONAL_RESOURCES_PATH_TAG_PATH,
            request.model_dump(by_alias=True),
        )

    async def mount_resources_to_group(
        self,
        *,
        request: MountResourcesToGroupRequest,
    ) -> ResourcePlacementResult:
        return await self._post_placement(
            _MOUNT_RESOURCES_TO_GROUP_PATH,
            request.model_dump(by_alias=True),
        )

    async def move_resources_in_group(
        self,
        *,
        request: MoveResourcesInGroupRequest,
    ) -> ResourcePlacementResult:
        return await self._post_placement(
            _MOVE_RESOURCES_IN_GROUP_PATH,
            request.model_dump(by_alias=True),
        )

    async def unmount_resources_from_group(
        self,
        *,
        request: UnmountResourcesFromGroupRequest,
    ) -> ResourcePlacementResult:
        return await self._post_placement(
            _UNMOUNT_RESOURCES_TO_GROUP_PATH,
            request.model_dump(by_alias=True),
        )

    async def move_personal_resources_to_trash(
        self,
        *,
        request: MovePersonalResourcesToTrashRequest,
    ) -> ResourcePlacementResult:
        return await self._post_placement(
            _MOVE_PERSONAL_RESOURCES_TO_TRASH_PATH,
            request.model_dump(by_alias=True),
        )

    async def _post_placement(self, path: str, body: dict[str, Any]) -> ResourcePlacementResult:
        data = await self._rpc.post(self._service_name, path, json=body)
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=path,
                msg=f"unexpected data payload: {data!r}",
            )
        return ResourcePlacementResult.model_validate(data)


__all__ = ["ResourceClient"]
