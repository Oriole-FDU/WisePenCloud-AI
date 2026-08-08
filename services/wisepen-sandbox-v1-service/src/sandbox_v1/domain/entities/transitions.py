from __future__ import annotations

from sandbox_v1.domain.entities.models import SandboxState


# 沙箱状态机白名单，Mongo repository 和其他持久化实现共享同一规则。
SANDBOX_ALLOWED_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.CREATING: frozenset({SandboxState.WARMING, SandboxState.DESTROYING}),
    SandboxState.WARMING: frozenset({SandboxState.READY, SandboxState.DESTROYING}),
    SandboxState.READY: frozenset({SandboxState.USER_ACTIVE, SandboxState.DESTROYING}),
    SandboxState.USER_ACTIVE: frozenset(
        {SandboxState.RETIRING, SandboxState.DESTROYING}
    ),
    SandboxState.RETIRING: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.LOST}),
    SandboxState.DESTROYED: frozenset(),
    SandboxState.LOST: frozenset(),
}


def can_transition(expected: SandboxState, state: SandboxState) -> bool:
    """判断 expected -> state 是否是合法沙箱状态转换。"""

    return state in SANDBOX_ALLOWED_TRANSITIONS[expected]
