from __future__ import annotations

from collections import Counter
from time import monotonic
from typing import Any


class MetricsCollector:
    """轻量进程内指标收集器。

    Counter 保存离散计数和值，duration 聚合耗时总量和样本数，readiness 额外记录
    首次降级时间。当前用于内部 `/pool/metrics` 快照；未来接 Prometheus 时可沿用
    同名指标。
    """

    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._durations: Counter[str] = Counter()
        self._duration_counts: Counter[str] = Counter()
        self._readiness_degraded_since: float | None = None

    def increment(self, name: str, amount: int = 1) -> None:
        """递增一个计数指标。"""

        self._counters[name] += amount

    def set_value(self, name: str, value: int | float) -> None:
        """设置一个可被快照直接读取的当前值指标。"""

        self._counters[name] = value

    def observe_ms(self, name: str, value_ms: float) -> None:
        """记录一次毫秒级耗时样本，并维护总耗时和样本数。"""

        self._durations[name] += int(value_ms)
        self._duration_counts[name] += 1

    def readiness(self, ready: int, min_ready: int) -> str:
        """根据 READY 数量返回就绪状态，并维护 degraded 起始时间。"""

        if ready >= min_ready:
            self._readiness_degraded_since = None
            return "ready"
        if self._readiness_degraded_since is None:
            # 只在首次降级时记录时间，用于计算持续 degraded 秒数。
            self._readiness_degraded_since = monotonic()
        return "degraded"

    def snapshot(
        self, ready: int, min_ready: int, target_ready: int = 0
    ) -> dict[str, Any]:
        """生成当前进程内指标快照。"""

        # 失败率需要分母，未发生尝试时按 0.0 处理。
        warmup_attempts = self._counters["warmup_attempts"]
        destroy_attempts = self._counters["destroy_attempts"]
        return {
            # 基础 counter 和当前值指标。
            **self._counters,
            # duration 总量与样本数，调用方可以看总耗时和采样规模。
            **{f"duration_ms_{key}": value for key, value in self._durations.items()},
            **{
                f"duration_count_{key}": value
                for key, value in self._duration_counts.items()
            },
            # duration 均值只在样本数非零时输出。
            **{
                f"duration_avg_ms_{key}": self._durations[key] / value
                for key, value in self._duration_counts.items()
                if value
            },
            # warmup/destroy 失败率用于快速判断生命周期路径是否异常。
            "warmup_failure_rate": (
                self._counters["warmup_failures"] / warmup_attempts
                if warmup_attempts else 0.0
            ),
            "destroy_failure_rate": (
                self._counters["destroy_failures"] / destroy_attempts
                if destroy_attempts else 0.0
            ),
            # READY 水位和 readiness 是健康检查与监控面板的核心字段。
            "ready_count": ready,
            "min_ready": min_ready,
            "target_ready": target_ready,
            "readiness": self.readiness(ready, min_ready),
            # degraded_seconds 只在持续低于 min_ready 时累积。
            "degraded_seconds": (
                monotonic() - self._readiness_degraded_since
                if self._readiness_degraded_since is not None else 0.0
            ),
        }
