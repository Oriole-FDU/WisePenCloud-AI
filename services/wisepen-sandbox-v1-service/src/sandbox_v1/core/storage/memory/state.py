from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sandbox_v1.domain.entities import (
    SandboxRecord,
    UserSandboxBindingRecord,
)
from sandbox_v1.domain.interfaces.metrics import MetricsPort


@dataclass
class _RepositoryState:
    """MemorySandboxRepository 共享的进程内状态。

    records 保存沙箱生命周期记录，user_bindings/sandbox_bindings 保存用户绑定和
    反向索引，generation 与 empty_checkouts 用于快照观测。所有读写都通过同一
    lock 保护，保持进程内原子性。
    """

    metrics: MetricsPort

    # Repository 的全局互斥锁，保护以下所有内存索引和计数。
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # 沙箱记录和用户绑定索引。
    records: dict[str, SandboxRecord] = field(default_factory=dict)
    user_bindings: dict[str, UserSandboxBindingRecord] = field(default_factory=dict)
    sandbox_bindings: dict[str, str] = field(default_factory=dict)

    # 快照观测字段：generation 代表状态变化次数，empty_checkouts 代表空池消费次数。
    generation: int = 0
    empty_checkouts: int = 0
