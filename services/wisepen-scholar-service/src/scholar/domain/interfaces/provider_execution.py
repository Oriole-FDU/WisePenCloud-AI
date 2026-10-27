"""适配器本次调用的排队观测；不属于论文事实，不进入来源缓存。"""

from __future__ import annotations

from contextvars import ContextVar

# 应用每次来源调用设置/恢复作用域；共享在途请求的等待者读取同一观测结果。
provider_queued_ahead: ContextVar[int | None] = ContextVar(
    "provider_queued_ahead", default=None
)
