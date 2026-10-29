"""应用层向消息消费者提供本次运行的事件处理器。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol


class MessageConsumer(Protocol):
    async def run(
        self,
        handler: Callable[[dict[str, object]], Awaitable[object]],
        *,
        started: asyncio.Event | None = None,
    ) -> None:
        """运行消费循环；started 在底层连接就绪后设置。"""
        ...
