from __future__ import annotations

from types import SimpleNamespace

from chat.service_client.mcp_service_client import _stringify_tool_result


def test_mcp_result_prefers_error_content_over_null_structured_content() -> None:
    result = SimpleNamespace(
        content=[SimpleNamespace(text="SESSION_BUSY: 同一 session 已有活动 turn")],
        structuredContent=None,
        isError=True,
    )

    assert _stringify_tool_result(result) == "SESSION_BUSY: 同一 session 已有活动 turn"


def test_mcp_result_falls_back_to_structured_content() -> None:
    result = SimpleNamespace(
        content=[],
        structuredContent={"lease_id": "lease-1"},
        isError=False,
    )

    assert _stringify_tool_result(result) == '{"lease_id": "lease-1"}'
