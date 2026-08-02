from __future__ import annotations

from typing import Any, Mapping


def timeout_seconds_from_ms(
    arguments: Mapping[str, Any],
    *,
    default_timeout_ms: int,
    max_timeout_ms: int,
    grace_seconds: float,
) -> float:
    value = arguments.get("timeout_ms")
    timeout_ms = default_timeout_ms if value is None else value
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise ValueError("timeout_ms must be an integer")
    if timeout_ms < 1 or timeout_ms > max_timeout_ms:
        raise ValueError(f"timeout_ms must be between 1 and {max_timeout_ms}")
    return timeout_ms / 1000 + grace_seconds
