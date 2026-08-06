from __future__ import annotations

from collections import Counter
from time import monotonic
from typing import Any


class MetricsCollector:
    """轻量进程内指标收集器。

    当前用于内部 `/pool/metrics` 快照；未来接 Prometheus 时可沿用同名指标。
    """

    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._durations: Counter[str] = Counter()
        self._duration_counts: Counter[str] = Counter()
        self._readiness_degraded_since: float | None = None

    def increment(self, name: str, amount: int = 1) -> None:
        self._counters[name] += amount

    def set_value(self, name: str, value: int | float) -> None:
        self._counters[name] = value

    def observe_ms(self, name: str, value_ms: float) -> None:
        self._durations[name] += int(value_ms)
        self._duration_counts[name] += 1

    def readiness(self, ready: int, min_ready: int) -> str:
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
        warmup_attempts = self._counters["warmup_attempts"]
        destroy_attempts = self._counters["destroy_attempts"]
        return {
            **self._counters,
            **{f"duration_ms_{key}": value for key, value in self._durations.items()},
            **{
                f"duration_count_{key}": value
                for key, value in self._duration_counts.items()
            },
            **{
                f"duration_avg_ms_{key}": self._durations[key] / value
                for key, value in self._duration_counts.items()
                if value
            },
            "warmup_failure_rate": (
                self._counters["warmup_failures"] / warmup_attempts
                if warmup_attempts else 0.0
            ),
            "destroy_failure_rate": (
                self._counters["destroy_failures"] / destroy_attempts
                if destroy_attempts else 0.0
            ),
            "ready_count": ready,
            "min_ready": min_ready,
            "target_ready": target_ready,
            "readiness": self.readiness(ready, min_ready),
            "degraded_seconds": (
                monotonic() - self._readiness_degraded_since
                if self._readiness_degraded_since is not None else 0.0
            ),
        }
