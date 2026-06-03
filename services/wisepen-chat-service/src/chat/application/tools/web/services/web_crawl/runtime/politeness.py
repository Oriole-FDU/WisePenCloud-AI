from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict
from urllib.parse import urlparse


@dataclass(slots=True)
class HostState:
    """单个主机的状态，包含互斥锁、上次请求时间和阻塞标记。"""
    lock: asyncio.Lock
    last_request_at: float = 0.0
    blocked: bool = False


class PerHostPoliteness:
    """按主机维度控制请求频率的礼貌策略，避免对单一站点造成压力。"""

    def __init__(self, *, min_interval_seconds: float = 1.0):
        """初始化最小请求间隔和各主机状态字典。"""
        self._min_interval_seconds = min_interval_seconds
        self._states: Dict[str, HostState] = {}

    def mark_blocked(self, url: str) -> None:
        """将指定 URL 所在主机标记为阻塞状态。"""
        state = self._get_state(url)
        state.blocked = True

    def is_blocked(self, url: str) -> bool:
        """检查指定 URL 所在主机是否处于阻塞状态。"""
        return self._get_state(url).blocked

    async def wait_turn(self, url: str) -> None:
        """等待到当前主机允许发起下一次请求的时间点。

        通过计算距上次请求的时间差，若不足最小间隔则主动 sleep 等待。
        """
        state = self._get_state(url)
        async with state.lock:
            now = time.monotonic()
            elapsed = now - state.last_request_at
            wait_seconds = self._min_interval_seconds - elapsed
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            state.last_request_at = time.monotonic()

    def _get_state(self, url: str) -> HostState:
        """根据 URL 获取或创建对应主机的状态对象。"""
        host = (urlparse(url).hostname or "").lower()
        state = self._states.get(host)
        if state is None:
            state = HostState(lock=asyncio.Lock())
            self._states[host] = state
        return state