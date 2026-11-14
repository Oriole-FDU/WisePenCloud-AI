from __future__ import annotations

from typing import Any

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient
from wisepen_mcp.domain.entities import (
    GroupCreateRequest,
    GroupItemInfo,
    GroupMemberDetail,
    GroupResourceDailyMetric,
    MessageInfo,
    PageResult,
    UserDetailInfo,
    UserTaskStatus,
    WalletDetail,
    WalletTransactionRecord,
)


_USER_SERVICE_NAME = "wisepen-user-service"
_LIST_GROUPS_PATH = "/group/list"
_LIST_GROUP_MEMBERS_PATH = "/group/member/list"
_GET_GROUP_BASE_INFO_PATH = "/group/getGroupBaseInfo"
_CREATE_GROUP_PATH = "/group/addGroup"
_JOIN_GROUP_PATH = "/group/joinGroup"
_LIST_GROUP_RESOURCE_DAILY_METRICS_PATH = "/group/dashboard/listResourceDailyMetrics"
_GET_USER_INFO_PATH = "/user/getUserInfo"
_GET_WALLET_INFO_PATH = "/user/wallet/getUserWalletInfo"
_LIST_WALLET_TRANSACTIONS_PATH = "/user/wallet/listTransactions"
_LIST_MESSAGES_PATH = "/user/message/listMessages"
_GET_UNREAD_MESSAGE_COUNT_PATH = "/user/message/getUnreadMessageCount"
_LIST_TASK_STATUS_PATH = "/user/task/listTaskStatus"


class UserClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _USER_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def list_groups(
        self,
        *,
        group_role_filter: str,
        group_type: str | None,
        page: int,
        size: int,
    ) -> PageResult[GroupItemInfo]:
        params: dict[str, Any] = {
            "groupRoleFilter": group_role_filter,
            "page": page,
            "size": size,
        }
        if group_type:
            params["groupType"] = group_type
        data = await self._rpc.get(self._service_name, _LIST_GROUPS_PATH, params=params)
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_LIST_GROUPS_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return PageResult[GroupItemInfo].model_validate(data)

    async def list_group_members(
        self,
        *,
        group_id: str,
        page: int,
        size: int,
    ) -> PageResult[GroupMemberDetail]:
        data = await self._rpc.get(
            self._service_name,
            _LIST_GROUP_MEMBERS_PATH,
            params={"groupId": group_id, "page": page, "size": size},
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_LIST_GROUP_MEMBERS_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return PageResult[GroupMemberDetail].model_validate(data)

    async def get_group_base_info(self, *, group_id: str) -> GroupItemInfo:
        data = await self._rpc.get(
            self._service_name,
            _GET_GROUP_BASE_INFO_PATH,
            params={"groupId": group_id},
        )
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_GROUP_BASE_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return GroupItemInfo.model_validate(data)

    async def create_group(self, *, request: GroupCreateRequest) -> str:
        data = await self._rpc.post(
            self._service_name,
            _CREATE_GROUP_PATH,
            json=request.model_dump(by_alias=True, exclude_none=True),
        )
        if not isinstance(data, (str, int)) or str(data).strip() == "":
            raise RpcError(
                service_name=self._service_name,
                path=_CREATE_GROUP_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return str(data).strip()

    async def join_group(self, *, invite_code: str) -> None:
        await self._rpc.post(
            self._service_name,
            _JOIN_GROUP_PATH,
            json={"inviteCode": invite_code},
        )

    async def list_group_resource_daily_metrics(
        self,
        *,
        group_id: str,
        start_date: str,
        end_date: str,
    ) -> list[GroupResourceDailyMetric]:
        data = await self._rpc.get(
            self._service_name,
            _LIST_GROUP_RESOURCE_DAILY_METRICS_PATH,
            params={"groupId": group_id, "startDate": start_date, "endDate": end_date},
        )
        if not isinstance(data, list):
            raise RpcError(
                service_name=self._service_name,
                path=_LIST_GROUP_RESOURCE_DAILY_METRICS_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return [GroupResourceDailyMetric.model_validate(item) for item in data]

    async def get_my_user_info(self) -> UserDetailInfo:
        data = await self._rpc.get(self._service_name, _GET_USER_INFO_PATH)
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_USER_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return UserDetailInfo.model_validate(data)

    async def get_my_wallet_info(self) -> WalletDetail:
        data = await self._rpc.get(self._service_name, _GET_WALLET_INFO_PATH)
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_GET_WALLET_INFO_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return WalletDetail.model_validate(data)

    async def list_my_wallet_transactions(
        self,
        *,
        group_id: str | None,
        wallet_transaction_types: list[str] | None,
        wallet_business_type: str | None,
        page: int,
        size: int,
    ) -> PageResult[WalletTransactionRecord]:
        params: dict[str, Any] = {"page": page, "size": size}
        if group_id is not None:
            params["groupId"] = group_id
        if wallet_transaction_types:
            params["walletTransactionTypes"] = wallet_transaction_types
        if wallet_business_type:
            params["walletBusinessType"] = wallet_business_type
        data = await self._rpc.get(self._service_name, _LIST_WALLET_TRANSACTIONS_PATH, params=params)
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_LIST_WALLET_TRANSACTIONS_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return PageResult[WalletTransactionRecord].model_validate(data)

    async def list_my_messages(self, *, page: int, size: int) -> PageResult[MessageInfo]:
        data = await self._rpc.get(self._service_name, _LIST_MESSAGES_PATH, params={"page": page, "size": size})
        if not isinstance(data, dict):
            raise RpcError(
                service_name=self._service_name,
                path=_LIST_MESSAGES_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return PageResult[MessageInfo].model_validate(data)

    async def get_unread_message_count(self) -> int:
        data = await self._rpc.get(self._service_name, _GET_UNREAD_MESSAGE_COUNT_PATH)
        if not isinstance(data, (int, str)) or str(data).strip() == "":
            raise RpcError(
                service_name=self._service_name,
                path=_GET_UNREAD_MESSAGE_COUNT_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return int(str(data).strip())

    async def list_my_task_status(self) -> list[UserTaskStatus]:
        data = await self._rpc.get(self._service_name, _LIST_TASK_STATUS_PATH)
        if not isinstance(data, list):
            raise RpcError(
                service_name=self._service_name,
                path=_LIST_TASK_STATUS_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return [UserTaskStatus.model_validate(item) for item in data]


__all__ = ["UserClient"]
