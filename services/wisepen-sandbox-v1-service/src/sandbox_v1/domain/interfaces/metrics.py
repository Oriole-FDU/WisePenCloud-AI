from __future__ import annotations

from typing import Any, Protocol


class MetricsPort(Protocol):
    """应用层使用的指标端口。"""

    def increment(self, name: str, amount: int = 1) -> None:
        """递增一个计数指标。"""
        ...

    def set_value(self, name: str, value: int | float) -> None:
        """设置一个当前值指标。"""
        ...

    def observe_ms(self, name: str, value_ms: float) -> None:
        """记录一次毫秒级耗时样本。"""
        ...

    def readiness(self, ready: int, min_ready: int) -> str:
        """根据 READY 水位返回就绪状态。"""
        ...

    def snapshot(self, ready: int, min_ready: int, target_ready: int = 0) -> dict[str, Any]:
        """生成指标快照，并合并池水位信息。"""
        ...
