import asyncio
from typing import Optional

from common.logger import log_error, log_event

from chat.application.skill_matcher import SkillMatcher


class SkillCacheRefresher:
    """
    Skill matcher 缓存的刷新调度器
    在启动阶段触发一次 eager warmup（作为"周期刷新的第 0 次"）
    之后每 ttl_seconds 调一次 matcher.warmup()，使得用户发布的 Skill 变化能在 TTL 内被当前副本感知
    """

    def __init__(self, matcher: SkillMatcher, ttl_seconds: int) -> None:
        self._matcher = matcher
        self._ttl = max(1, ttl_seconds)
        self._task: Optional[asyncio.Task] = None
        # 所有刷新入口共享同一把锁，保证 single-flight
        # TTL tick 与未来的 Kafka 事件不会并发调 matcher.warmup
        self._lock = asyncio.Lock()
        # stop() 通过 set 此 Event 触发循环退出
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """
        启动刷新器
        先同步完成一次 eager warmup，再挂起周期循环
        重复调用直接返回
        """
        if self._task is not None:
            return
        self._stopping.clear()
        # 初始装载 = 周期刷新的第 0 次，保证启动后立即可用，
        # 避免"启动后前 TTL 秒 cache 空窗"的隐患。
        # 失败由 trigger/matcher 内部 catch，不阻塞服务启动。
        await self.trigger()
        self._task = asyncio.create_task(self._tick_loop(), name="skill-cache-refresher")
        log_event("Skill cache refresher 已启动", ttl_seconds=self._ttl)

    async def stop(self) -> None:
        """
        停止刷新器
        """
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            # shutdown 语义：尽力回收，不再向上抛
            pass
        self._task = None
        log_event("Skill cache refresher 已停止")

    async def trigger(self) -> None:
        """
        触发一次刷新
        """
        async with self._lock:
            try:
                await self._matcher.warmup()
            except Exception as e:
                # matcher.warmup 自身已吞异常，这里是防御层兜底
                log_error("Skill cache refresh", e)

    async def _tick_loop(self) -> None:
        """
        等待 TTL 或 stop 信号：TTL 到点 → 触发 refresh；stop 被 set → 立刻返回
        """
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._ttl)
                return  # stop 被调用，整体退出
            except asyncio.TimeoutError:
                pass  # TTL 到点，继续 refresh
            await self.trigger()
