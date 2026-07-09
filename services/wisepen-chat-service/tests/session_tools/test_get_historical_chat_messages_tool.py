from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("NACOS_SERVER_ADDR", "127.0.0.1:8848")

ranking_engine_stub = types.ModuleType("chat.application.utils.ranking_engine")
ranking_engine_stub.__path__ = [
    str(
        Path(__file__).resolve().parents[2]
        / "src"
        / "chat"
        / "application"
        / "utils"
        / "ranking_engine"
    )
]
sys.modules.setdefault("chat.application.utils.ranking_engine", ranking_engine_stub)

ranking_engine_registry_stub = types.ModuleType("chat.application.utils.ranking_engine.registry")
ranking_engine_registry_stub.get_ranking_engine = lambda _: object()
sys.modules.setdefault("chat.application.utils.ranking_engine.registry", ranking_engine_registry_stub)

from chat.application.tools.core import ToolExecutionError
from chat.application.tools.session_tools.get_historical_chat_messages_tool import (
    GetHistoricalChatMessagesTool,
)


class _FakeMessageRepository:
    async def search_messages_by_text(self, **_: object) -> list[object]:
        return []


@pytest.mark.asyncio
async def test_get_historical_chat_messages_requires_session_id() -> None:
    tool = GetHistoricalChatMessagesTool(
        message_repo=_FakeMessageRepository(),
        max_output_chars=1000,
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute({}, keyword="hello")

    assert exc_info.value.reason == "missing_session_id"


@pytest.mark.asyncio
async def test_get_historical_chat_messages_requires_non_blank_keyword() -> None:
    tool = GetHistoricalChatMessagesTool(
        message_repo=_FakeMessageRepository(),
        max_output_chars=1000,
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute({"session_id": "s1"}, keyword="   ")

    assert exc_info.value.reason == "missing_keyword"
