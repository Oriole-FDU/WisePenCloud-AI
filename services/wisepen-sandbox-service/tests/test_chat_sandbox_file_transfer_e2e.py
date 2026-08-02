from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_chat_sandbox_file_transfer_e2e.py"
SPEC = importlib.util.spec_from_file_location("run_chat_sandbox_file_transfer_e2e", SCRIPT_PATH)
assert SPEC and SPEC.loader
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)


def model(model_id: str) -> dict:
    return {"id": model_id, "is_active": True, "support_tools": True, "mappings": [{"provider_id": f"provider-{model_id}", "is_active": True}]}


def args(**overrides) -> Namespace:
    values = {
        "sandbox_url": "http://sandbox", "chat_url": "http://chat", "source": "source-secret",
        "user_id": "same-user", "request_id": "request-1", "model_id": None,
        "provider_id": None, "developer": "developer", "sandbox_fixture_host": "fixture-host",
        "fixture_bind_host": "127.0.0.1",
    }
    values.update(overrides)
    return Namespace(**values)


def events(*, tools: list[str], output: str) -> str:
    values = [f'data: {{"type":"tool-input-available","toolName":"{tool}","input":{{"command":"{output}"}}}}' for tool in tools]
    values.extend([
        '{"type":"tool-output-available","output":"' + output + '"}',
        '{"type":"finish"}',
    ])
    return "\n".join("data: " + value if not value.startswith("data:") else value for value in values)


def test_fixture_source_and_upload_contracts_are_isolated(monkeypatch):
    cases = [
        {"name": "session-one", "source_path": "/source/session-one.txt", "upload_path": "/upload/session-one.txt"},
        {"name": "session-two", "source_path": "/source/session-two.txt", "upload_path": "/upload/session-two.txt"},
    ]
    def fake_server_init(server, *_args, **_kwargs):
        server.server_address = ("127.0.0.1", 12345)

    monkeypatch.setattr(e2e.ThreadingHTTPServer, "__init__", fake_server_init)
    fixture = e2e.FileTransferFixture("127.0.0.1")
    assert fixture.read_source(cases[0]["source_path"]) == fixture.sources[cases[0]["source_path"]]
    assert fixture.read_source("/unknown") is None
    for case in cases:
        expected = (fixture.sources[case["source_path"]].decode().upper() + f"processed-by={case['name']}\n").encode()
        assert fixture.capture_upload(case["upload_path"], expected)
    assert not fixture.capture_upload("/unknown", b"ignored")
    fixture.assert_uploads(cases)


class FakeFixture:
    def __init__(self, _bind_host: str) -> None:
        self.port = 12345

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def url(self, host: str, path: str) -> str:
        return f"http://{host}:{self.port}{path}"

    def assert_uploads(self, _cases):
        return None


def test_assert_turn_requires_tools_markers_and_finish(capsys):
    raw = "\n".join([
        'data: {"type":"tool-input-available","toolName":"shell_exec","input":{"command":"http://fixture/source"}}',
        'data: {"type":"tool-input-available","toolName":"run_sandbox_script","input":{"language":"python"}}',
        'data: {"type":"tool-output-available","output":"E2E_DOWNLOAD_CURL_MS=2.4 E2E_TRANSFORM_MARKER duration_ms: 8"}',
        'data: {"type":"finish"}',
    ])
    parsed = e2e.parse_sse(raw)
    e2e._assert_turn(parsed, tools={"shell_exec", "run_sandbox_script"}, input_contains=("http://fixture/source",), output_markers={"E2E_DOWNLOAD_CURL_MS", "E2E_TRANSFORM_MARKER"}, label="download")
    output = capsys.readouterr().out
    assert "sandbox_curl_duration_ms" in output
    assert "sandbox_script_duration_ms" in output
    with pytest.raises(RuntimeError, match="did not finish"):
        e2e._assert_turn(parsed[:-1], tools={"shell_exec"}, input_contains=("fixture",), output_markers=set(), label="bad")


def test_download_query_uses_fixed_timing_marker_and_session_specific_transform():
    query = e2e._download_query("http://fixture/source", "input.txt", "session-one")

    assert "E2E_DOWNLOAD_CURL_MS=%{time_total}" in query
    assert "processed-by=session-one" in query
    assert "-o 'input.txt'" in query
    assert "output.txt" in query
    assert "/home/gem/" not in query


def test_assert_turn_rejects_internal_container_paths():
    parsed = e2e.parse_sse("\n".join([
        'data: {"type":"tool-input-available","toolName":"shell_exec","input":{"command":"cat /home/gem/workspaces/user/session/input.txt"}}',
        'data: {"type":"finish"}',
    ]))

    with pytest.raises(RuntimeError, match="internal container path"):
        e2e._assert_turn(parsed, tools={"shell_exec"}, input_contains=(), output_markers=set(), label="invalid-path")


def test_main_runs_four_turns_for_same_user_and_cleans_up(monkeypatch):
    requests: list[tuple[str, dict]] = []
    created = iter(["session-one-id", "session-two-id"])

    def chat_request(_url, path, _method, **kwargs):
        requests.append((path, kwargs))
        if path == "/chat/model/listAvailableModels":
            return {"code": 200, "data": {"system_models": [model("model-a")], "user_models": []}}
        if path == "/chat/session/createSession":
            return {"code": 200, "data": {"id": next(created)}}
        return {"code": 200, "data": None}

    turn = {"value": 0}
    def fake_run(command, *, label):
        if command[0] == "docker":
            return "ready-container\n"
        if command[1] == str(e2e.METRICS_SCRIPT):
            bindings = 1 if label == "pool_metrics_after" else 0
            return json.dumps({
                "code": 200,
                "data": {
                    "running": 0,
                    "active_user_bindings": 0,
                    "idle_user_bindings": bindings,
                },
            })
        if command[1] == str(e2e.CHAT_SCRIPT):
            turn["value"] += 1
            query = command[command.index("--query") + 1]
            assert command[command.index("--user-id") + 1] == "same-user"
            if "--upload-file" in query:
                return "\n".join([
                    "data: " + json.dumps({"type": "tool-input-available", "toolName": "shell_exec", "input": {"command": query}}),
                    'data: {"type":"tool-output-available","output":{"sandbox_id":"shared-user-sandbox","text":"E2E_UPLOAD_CURL_MS=1.2"}}',
                    'data: {"type":"finish"}',
                ])
            return "\n".join([
                "data: " + json.dumps({"type": "tool-input-available", "toolName": "shell_exec", "input": {"command": query}}),
                'data: {"type":"tool-input-available","toolName":"run_sandbox_script","input":{"language":"python"}}',
                'data: {"type":"tool-output-available","output":{"sandbox_id":"shared-user-sandbox","text":"E2E_DOWNLOAD_CURL_MS=1.1 E2E_TRANSFORM_MARKER duration_ms: 3"}}',
                'data: {"type":"finish"}',
            ])
        raise AssertionError(command)

    monkeypatch.setattr(e2e, "chat_json_request", chat_request)
    monkeypatch.setattr(
        e2e,
        "sandbox_json_request",
        lambda *_args, **_kwargs: {"code": 200, "data": {"status": "destroyed"}},
    )
    monkeypatch.setattr(e2e, "run", fake_run)
    monkeypatch.setattr(e2e, "FileTransferFixture", FakeFixture)
    assert e2e.main(args()) == 0
    assert turn["value"] == 4
    assert [path for path, _ in requests] == [
        "/chat/model/listAvailableModels", "/chat/session/createSession", "/chat/session/createSession",
        "/chat/session/deleteSession", "/chat/session/deleteSession",
    ]
    assert all(kwargs["user_id"] == "same-user" for _path, kwargs in requests)


def test_main_cleans_both_sessions_after_a_turn_failure(monkeypatch):
    calls: list[str] = []
    created = iter(["session-one-id", "session-two-id"])

    def chat_request(_url, path, _method, **_kwargs):
        calls.append(path)
        if path == "/chat/model/listAvailableModels":
            return {"code": 200, "data": {"system_models": [model("model-a")], "user_models": []}}
        if path == "/chat/session/createSession":
            return {"code": 200, "data": {"id": next(created)}}
        return {"code": 200, "data": None}

    monkeypatch.setattr(e2e, "chat_json_request", chat_request)
    monkeypatch.setattr(
        e2e,
        "sandbox_json_request",
        lambda *_args, **_kwargs: {"code": 200, "data": {"status": "destroyed"}},
    )
    monkeypatch.setattr(
        e2e,
        "run",
        lambda command, *, label: (
            '{"code": 200, "data": {"running": 0, "active_user_bindings": 0, "idle_user_bindings": 0}}'
            if label == "pool_metrics_before"
            else "ready-container\n"
            if command[0] == "docker"
            else (_ for _ in ()).throw(RuntimeError("sandbox unavailable"))
        ),
    )
    monkeypatch.setattr(e2e, "FileTransferFixture", FakeFixture)
    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        e2e.main(args())
    assert calls[-2:] == ["/chat/session/deleteSession", "/chat/session/deleteSession"]
