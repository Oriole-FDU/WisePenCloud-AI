from datetime import datetime
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 27, 9, 8, 7, tzinfo=tz)


class _ChatMessage:
    def __init__(self, session_id, role, content):
        self.session_id = session_id
        self.role = role
        self.content = content


@pytest.fixture
def chat_context_assembler(monkeypatch):
    config_module = ModuleType("chat.core.config.app_settings")
    config_module.settings = SimpleNamespace()
    monkeypatch.setitem(sys.modules, config_module.__name__, config_module)
    monkeypatch.delitem(sys.modules, "chat.application.chat_context_assembler", raising=False)

    return importlib.import_module("chat.application.chat_context_assembler")


def test_assemble_prompt_includes_current_shanghai_time(chat_context_assembler, monkeypatch):
    monkeypatch.setattr(chat_context_assembler, "datetime", _FixedDatetime)
    monkeypatch.setattr(chat_context_assembler, "ChatMessage", _ChatMessage)
    assembler = chat_context_assembler.ChatContextAssembler(
        message_repo=None,
        session_repo=None,
        hot_context_repo=None,
    )

    messages = assembler.assemble_prompt(
        session_id="session-1",
        user_query="What time is it?",
        system_prompt="You are WisePen.",
        session_summary=None,
        history_messages=[],
        relevant_facts=[],
    )

    assert messages[0].role == chat_context_assembler.Role.SYSTEM
    assert messages[0].content == (
        "You are WisePen.\n\n"
        "Current time: 2026-07-27 09:08:07 CST+0800. Answer with this current time in mind."
    )
