from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import litellm
from chat.application.token_counter import TokenCounter
from chat.domain.entities import Role


@pytest.mark.asyncio
async def test_count_messages_uses_local_tokenizer(monkeypatch):
    calls = {}

    def local_counter(**kwargs):
        calls.update(kwargs)
        return 7

    remote_counter = AsyncMock(side_effect=AssertionError("remote token API must not be called"))
    monkeypatch.setattr(litellm, "token_counter", local_counter)
    monkeypatch.setattr(litellm, "acount_tokens", remote_counter)

    message = SimpleNamespace(role=Role.USER, content="hello")
    result = await TokenCounter().count_messages([message], model_name="custom-model")

    assert result == 7
    assert calls["model"] == "custom-model"
    remote_counter.assert_not_awaited()
