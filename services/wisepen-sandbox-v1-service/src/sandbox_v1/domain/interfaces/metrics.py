from __future__ import annotations

from typing import Any, Protocol


class MetricsPort(Protocol):
    def increment(self, name: str, amount: int = 1) -> None:
        ...

    def set_value(self, name: str, value: int | float) -> None:
        ...

    def observe_ms(self, name: str, value_ms: float) -> None:
        ...

    def readiness(self, ready: int, min_ready: int) -> str:
        ...

    def snapshot(self, ready: int, min_ready: int, target_ready: int = 0) -> dict[str, Any]:
        ...
