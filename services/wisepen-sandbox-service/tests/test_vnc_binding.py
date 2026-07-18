from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sandbox.gateway.binding import VncBinding


class FakeSession:
    def __init__(self) -> None:
        self.release_calls: list[tuple[str, str]] = []

    async def acquire_for(self, user_id: str, session_id: str):
        await asyncio.sleep(0)
        return SimpleNamespace(
            sandbox_id="sandbox-1",
            endpoint=SimpleNamespace(base_url="http://127.0.0.1:1234"),
        )

    async def release_for(self, user_id: str, session_id: str) -> None:
        self.release_calls.append((user_id, session_id))


@pytest.mark.asyncio
async def test_vnc_binding_is_idempotent_and_release_is_safe() -> None:
    session = FakeSession()
    binding = VncBinding(session)

    first, second = await asyncio.gather(
        binding.acquire("user-1", "session-1"),
        binding.acquire("user-1", "session-1"),
    )

    assert first == second
    assert first.vnc_url == "http://127.0.0.1:1234/vnc/index.html?autoconnect=true"
    assert (await binding.stats())["active_bindings"] == 1

    await binding.release("user-1", "session-1")
    await binding.release("user-1", "session-1")
    assert session.release_calls == [("user-1", "session-1")]
