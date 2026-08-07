from __future__ import annotations

from sandbox_v1.domain.entities.models import SandboxState


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
    return state in SANDBOX_ALLOWED_TRANSITIONS[expected]
