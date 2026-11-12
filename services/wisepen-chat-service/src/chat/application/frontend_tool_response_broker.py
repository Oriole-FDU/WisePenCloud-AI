from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from chat.application.tools.core.definition import ClientToolResult, ToolApprovalStatus
from chat.core.constants import KAFKA_CHAT_TURN_RESPONSE_TOPIC
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from common.kafka.producer import KafkaProducerClient
from common.logger import error

if TYPE_CHECKING:
    from chat.core.persistence.redis.frontend_tool_response import RedisFrontendToolResponseRepository


FrontendToolResults = tuple[list[ClientToolResult], list[ToolApprovalStatus]]

# 超时后 submit 已认领等待权时，再给 Kafka 投递留的宽限窗口，避免消息丢失导致永久挂起
_KAFKA_DELIVERY_GRACE_SECONDS = 5


@dataclass
class FrontendToolResponseWait:
    session_id: str
    expected_client_tool_call_ids: tuple[str, ...]
    expected_approval_tool_call_ids: tuple[str, ...]
    future: asyncio.Future[FrontendToolResults]


class FrontendToolResponseBroker:
    def __init__(
        self,
        *,
        response_repo: RedisFrontendToolResponseRepository,
        kafka_producer: KafkaProducerClient,
        timeout_seconds: int,
    ) -> None:
        self._response_repo = response_repo
        self._kafka_producer = kafka_producer
        self._timeout_seconds = timeout_seconds
        self._waiters: dict[str, FrontendToolResponseWait] = {}

    async def open_wait(
        self,
        *,
        session_id: str,
        client_tool_call_ids: list[str],
        approval_tool_call_ids: list[str],
    ) -> FrontendToolResponseWait:
        """建立对前端工具结果的等待，runner 在发出 ToolInput/ToolApproval 事件前调用"""
        if session_id in self._waiters:
            raise RuntimeError(f"session {session_id} already has a frontend response waiter")

        # 在当前事件循环上建一个 Future，以备将来存储收到的前端工具结果
        future: asyncio.Future[FrontendToolResults] = asyncio.get_running_loop().create_future()
        waiter = FrontendToolResponseWait(
            session_id=session_id,
            expected_client_tool_call_ids=tuple(client_tool_call_ids),
            expected_approval_tool_call_ids=tuple(approval_tool_call_ids),
            future=future,
        )
        self._waiters[session_id] = waiter

        try:
            # redis 写入等待标记
            await self._response_repo.register_wait(
                session_id,
                list(waiter.expected_client_tool_call_ids),
                list(waiter.expected_approval_tool_call_ids),
            )
        except Exception:
            self._waiters.pop(session_id, None)
            raise
        return waiter

    async def receive(
        self,
        waiter: FrontendToolResponseWait,
        *,
        cancel_requested: Callable[[], Awaitable[bool]] | None = None,
    ) -> FrontendToolResults | None:
        """等待接收端工具调用结果
        收到前端工具调用结果时返回 FrontendToolResults，超时返回 None
        """
        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        # 认领失败说明 submit 已拿走等待权、正在投递；再给投递一小段宽限，仍不到就按超时处理
        delivery_deadline: float | None = None
        try:
            while True:
                if cancel_requested is not None and await cancel_requested():
                    raise asyncio.CancelledError

                # 检查剩余时间并等待
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining > 0:
                    try:
                        # 等待前端工具结果
                        return await asyncio.wait_for(
                            asyncio.shield(waiter.future),
                            timeout=min(remaining, 1.0),
                        )
                    except asyncio.TimeoutError:
                        continue

                # 已经超时，认领等待
                # 如果认领成功，就是真超时，返回 None，否则可能 submit 已认领，继续等投递
                if await self._response_repo.claim(waiter.session_id):
                    return None

                if delivery_deadline is None:
                    delivery_deadline = asyncio.get_running_loop().time() + _KAFKA_DELIVERY_GRACE_SECONDS
                if asyncio.get_running_loop().time() >= delivery_deadline:
                    # 宽限窗口内仍然未能收到消息
                    return None

                try:
                    # 等待前端工具结果
                    return await asyncio.wait_for(
                        asyncio.shield(waiter.future),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            await self.discard(waiter)

    async def discard(self, waiter: FrontendToolResponseWait) -> None:
        # 清理本地 waiter
        # future 尚未 resolve 时（取消/超时）把 Redis 标记也认领掉
        if self._waiters.get(waiter.session_id) is waiter:
            self._waiters.pop(waiter.session_id, None)
        if not waiter.future.done():
            waiter.future.cancel()
            await self._response_repo.claim(waiter.session_id)

    async def is_waiting(self, session_id: str) -> bool:
        state = await self._response_repo.get_wait(session_id)
        return True if state is not None else False

    async def submit(
        self,
        *,
        session_id: str,
        client_tool_results: list[ClientToolResult],
        tool_approval_status: list[ToolApprovalStatus],
    ) -> None:
        """提交前端工具调用结果"""
        # 无等待状态，说明已经过期
        value = await self._response_repo.get_wait(session_id)
        if value is None:
            raise ServiceException(ChatErrorCode.CHAT_TOOL_RESPONSE_WAIT_EXPIRED)

        # 校验前端工具调用结果是否合法
        # 错投/过期响应在这里被拦下并返回 INVALID
        expected_clients = sorted(value.get("expected_client_tool_call_ids", []))
        expected_approvals = sorted(value.get("expected_approval_tool_call_ids", []))
        submitted_clients = sorted(item.tool_call_id for item in client_tool_results)
        submitted_approvals = sorted(item.tool_call_id for item in tool_approval_status)
        if submitted_clients != expected_clients or submitted_approvals != expected_approvals:
            raise ServiceException(ChatErrorCode.CHAT_TOOL_RESPONSE_INVALID)

        # 认领等待，如果不能认领说明已经超时
        if not await self._response_repo.claim(session_id):
            raise ServiceException(ChatErrorCode.CHAT_TOOL_RESPONSE_WAIT_EXPIRED)

        # 发送 Kafka 消息
        published = await self._kafka_producer.send(
            topic=KAFKA_CHAT_TURN_RESPONSE_TOPIC,
            value={
                "session_id": session_id,
                "client_tool_results": [asdict(item) for item in client_tool_results],
                "tool_approval_status": [asdict(item) for item in tool_approval_status],
            },
            key=session_id.encode("utf-8"),
        )
        # 未能发送，恢复等待以便后续重试
        if not published:
            await self._response_repo.register_wait(
                session_id,
                value["expected_client_tool_call_ids"],
                value["expected_approval_tool_call_ids"],
            )
            raise ServiceException(ChatErrorCode.CHAT_TOOL_RESPONSE_PUBLISH_FAILED)

    async def handle_message(self, message: dict[str, Any]) -> None:
        # Kafka 广播给每个 worker
        session_id = message.get("session_id")

        client_payload = message.get("client_tool_results")
        approval_payload = message.get("tool_approval_status")
        if not isinstance(client_payload, list) or not isinstance(approval_payload, list):
            return

        # 只有持有该 session waiter 的 worker 才 resolve，其余直接 return
        waiter = self._waiters.get(session_id) if isinstance(session_id, str) else None
        if waiter is None or waiter.future.done():
            return

        try:
            client_tool_results = [
                ClientToolResult(
                    tool_call_id=item["tool_call_id"],
                    is_error=item["is_error"],
                    output=item.get("output"),
                )
                for item in client_payload
            ]
            tool_approval_status = [
                ToolApprovalStatus(
                    tool_call_id=item["tool_call_id"],
                    approved=item["approved"],
                )
                for item in approval_payload
            ]
        except Exception as exc:
            error("frontend tool response payload could not be decoded.", session_id=session_id, exc=exc)
            return

        # 写入结果
        waiter.future.set_result((client_tool_results, tool_approval_status))
