from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def batched(items: Sequence[T], *, batch_size: int) -> tuple[tuple[T, ...], ...]:
    """按固定大小切分序列，并保留原始顺序。"""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")
    if not items:
        return ()
    return tuple(
        tuple(items[index : index + batch_size])
        for index in range(0, len(items), batch_size)
    )
