from __future__ import annotations

import importlib.util
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_chat_sandbox_e2e.py"
SPEC = importlib.util.spec_from_file_location("run_chat_sandbox_e2e", SCRIPT_PATH)
assert SPEC and SPEC.loader
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)


def model(model_id: str, *, tools: bool = True, mappings: list[dict] | None = None) -> dict:
    return {
        "id": model_id,
        "is_active": True,
        "support_tools": tools,
        "mappings": mappings if mappings is not None else [{"provider_id": f"provider-{model_id}", "is_active": True}],
    }


def available_models(system_models: list[dict], user_models: list[dict]) -> dict:
    return {"code": 200, "data": {"system_models": system_models, "user_models": user_models}}


def args(**overrides) -> Namespace:
    values = {
        "sandbox_url": "http://sandbox",
        "chat_url": "http://chat",
        "source": "source-secret",
        "user_id": "e2e-user",
        "session_id": None,
        "request_id": "request-1",
        "model_id": None,
        "provider_id": None,
        "developer": "yczhou23",
        "query": "run the sandbox probe",
    }
    values.update(overrides)
    return Namespace(**values)


def test_select_chat_model_prefers_system_model():
    selected = e2e.select_chat_model(
        available_models([model("system-model")], [model("user-model")])
    )

    assert selected == ("system-model", "provider-system-model")


def test_select_chat_model_skips_unsupported_and_inactive_mappings():
    selected = e2e.select_chat_model(
        available_models(
            [
                model("no-tools", tools=False),
                model("inactive-mapping", mappings=[{"provider_id": "disabled", "is_active": False}]),
            ],
            [model("user-model")],
        )
    )

    assert selected == ("user-model", "provider-user-model")


def test_select_chat_model_rejects_unusable_explicit_override():
    with pytest.raises(RuntimeError, match="No active tool-capable"):
        e2e.select_chat_model(
            available_models([model("model-a", mappings=[{"provider_id": "disabled", "is_active": False}])], []),
            model_id="model-a",
        )


def test_select_chat_model_rejects_when_no_tool_capable_model_exists():
    with pytest.raises(RuntimeError, match="No active tool-capable"):
        e2e.select_chat_model(
            available_models([model("model-a", tools=False)], [model("model-b", mappings=[])]),
        )


def test_display_command_redacts_source():
    displayed = e2e._display_command([
        "bash", "probe.sh", "--source", "source-secret", "--user-id", "e2e-user",
    ])

    assert "source-secret" not in displayed
    assert "--source <redacted>" in displayed
    assert "e2e-user" in displayed


def test_run_prints_success_diagnostics_and_stdout(monkeypatch, capsys):
    result = subprocess.CompletedProcess(
        ["bash", "probe.sh"], 0, "probe-output\n", "probe-warning\n"
    )
    monkeypatch.setattr(e2e.subprocess, "run", lambda *args, **kwargs: result)

    assert e2e.run(["bash", "probe.sh"], label="probe") == "probe-output\n"
    output = capsys.readouterr().out
    assert "[e2e] command=probe" in output
    assert "[e2e] exit_code=0" in output
    assert "[e2e] stdout_begin\nprobe-output\n[e2e] stdout_end" in output
    assert "[e2e] stderr_begin\nprobe-warning\n[e2e] stderr_end" in output


def test_run_failure_contains_diagnostics(monkeypatch, capsys):
    result = subprocess.CompletedProcess(
        ["bash", "probe.sh"], 1, "partial-output\n", "failure-detail\n"
    )
    monkeypatch.setattr(e2e.subprocess, "run", lambda *args, **kwargs: result)

    with pytest.raises(RuntimeError, match=r"command failed \[probe\]") as exc_info:
        e2e.run(["bash", "probe.sh", "--source", "source-secret"], label="probe")
    assert "partial-output" in str(exc_info.value)
    assert "failure-detail" in str(exc_info.value)
    assert "source-secret" not in capsys.readouterr().out


def test_print_sse_summary_includes_tool_input_and_output(capsys):
    e2e.print_sse_summary([
        {
            "type": "tool-input-available",
            "toolName": "run_sandbox_script",
            "toolCallId": "call-1",
            "input": {"language": "python", "code": "print('chat-mcp-curl-e2e')"},
        },
        {
            "type": "tool-output-available",
            "toolCallId": "call-1",
            "output": {
                "status": "succeeded",
                "request_id": "sandbox-request",
                "stdout": "chat-mcp-curl-e2e\n",
                "stderr": "",
                "exit_code": 0,
            },
        },
    ])
    output = capsys.readouterr().out
    assert 'type=tool-input-available' in output
    assert 'toolName="run_sandbox_script"' in output
    assert 'input={"language": "python", "code": "print(\'chat-mcp-curl-e2e\')"}' in output
    assert 'type=tool-output-available' in output
    assert 'status="succeeded"' in output
    assert 'request_id="sandbox-request"' in output
    assert 'stdout="chat-mcp-curl-e2e\\n"' in output
    assert 'exit_code=0' in output


def test_chat_json_request_sets_gateway_context_headers(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"code": 200, "data": {}}'

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(e2e, "urlopen", urlopen)

    assert e2e.chat_json_request(
        "http://chat",
        "/chat/session/deleteSession",
        "POST",
        source="source-secret",
        user_id="e2e-user",
        request_id="request-1",
        developer="yczhou23",
        query={"session_id": "created-session"},
    ) == {"code": 200, "data": {}}
    request = captured["request"]
    assert request.get_header("X-from-source") == "source-secret"
    assert request.get_header("X-user-id") == "e2e-user"
    assert request.get_header("X-request-id") == "request-1"
    assert request.get_header("X-developer") == "yczhou23"
    assert request.full_url.endswith("session_id=created-session")


def test_main_creates_and_cleans_up_temporary_session(monkeypatch):
    requests = []

    def chat_request(_url, path, _method, **kwargs):
        requests.append((path, kwargs))
        if path == "/chat/model/listAvailableModels":
            return available_models([model("model-a")], [])
        if path == "/chat/session/createSession":
            return {"code": 200, "data": {"id": "created-session"}}
        assert path == "/chat/session/deleteSession"
        return {"code": 200, "data": None}

    def fake_run(command, *, label):
        if command[1] == str(e2e.METRICS_SCRIPT):
            return '{"code": 200, "data": {"running": 0}}'
        if command[1] == str(e2e.CHAT_SCRIPT):
            assert "--session-id" in command and command[command.index("--session-id") + 1] == "created-session"
            assert "--provider-id" in command and command[command.index("--provider-id") + 1] == "provider-model-a"
            assert "--developer" in command and command[command.index("--developer") + 1] == "yczhou23"
            return "\n".join([
                'data: {"type":"tool-input-available","toolName":"run_sandbox_script"}',
                'data: {"type":"tool-output-available","output":{"status":"succeeded","request_id":"sandbox-request","stdout":"chat-mcp-curl-e2e"}}',
                'data: {"type":"finish"}',
                "data: [DONE]",
            ])
        return "mcp=PASS\n"

    monkeypatch.setattr(e2e, "chat_json_request", chat_request)
    monkeypatch.setattr(e2e, "run", fake_run)

    assert e2e.main(args()) == 0
    assert [path for path, _ in requests] == [
        "/chat/model/listAvailableModels",
        "/chat/session/createSession",
        "/chat/session/deleteSession",
    ]
    assert requests[-1][1]["query"] == {"session_id": "created-session"}


def test_main_cleans_up_created_session_after_failure(monkeypatch):
    calls = []

    def chat_request(_url, path, _method, **kwargs):
        calls.append(path)
        if path == "/chat/model/listAvailableModels":
            return available_models([model("model-a")], [])
        if path == "/chat/session/createSession":
            return {"code": 200, "data": {"id": "created-session"}}
        return {"code": 200, "data": None}

    monkeypatch.setattr(e2e, "chat_json_request", chat_request)
    monkeypatch.setattr(e2e, "run", lambda _command, *, label: (_ for _ in ()).throw(RuntimeError("sandbox unavailable")))

    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        e2e.main(args())
    assert calls[-1] == "/chat/session/deleteSession"


def test_main_does_not_delete_caller_session(monkeypatch):
    calls = []

    def chat_request(_url, path, _method, **kwargs):
        calls.append(path)
        return available_models([model("model-a")], [])

    monkeypatch.setattr(e2e, "chat_json_request", chat_request)
    monkeypatch.setattr(e2e, "run", lambda _command, *, label: (_ for _ in ()).throw(RuntimeError("sandbox unavailable")))

    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        e2e.main(args(session_id="caller-session"))
    assert calls == ["/chat/model/listAvailableModels"]
