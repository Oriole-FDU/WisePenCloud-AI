#!/usr/bin/env python3
"""Exercise two Chat sandbox workspaces with HTTP file download, processing, and upload."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
METRICS_SCRIPT = ROOT / "wisepen-sandbox-service" / "scripts" / "check_pool_metrics.sh"
CHAT_SCRIPT = ROOT / "wisepen-chat-service" / "scripts" / "run_chat_request.sh"
_CURL_TIMING_PATTERN = re.compile(r"E2E_(?:DOWNLOAD|UPLOAD)_CURL_MS=([0-9]+(?:\.[0-9]+)?)")
_SCRIPT_DURATION_PATTERN = re.compile(r"duration_ms:\s*([0-9]+(?:\.[0-9]+)?)")


def _display_command(command: list[str]) -> str:
    displayed: list[str] = []
    redact_next = False
    for value in command:
        if redact_next:
            displayed.append("<redacted>")
            redact_next = False
        else:
            displayed.append(value)
            redact_next = value in {"--source", "--from-source", "X-From-Source:"}
    return " ".join(displayed)


def _print_command_result(
    label: str, command: list[str], result: subprocess.CompletedProcess[str], duration_ms: float
) -> None:
    print(f"[e2e] command={label}")
    print(f"[e2e] argv={_display_command(command)}")
    print(f"[e2e] exit_code={result.returncode}")
    print(f"[e2e] duration_ms={duration_ms:.1f}")
    print("[e2e] stdout_begin")
    print(result.stdout, end="" if result.stdout.endswith("\n") or not result.stdout else "\n")
    print("[e2e] stdout_end")
    print("[e2e] stderr_begin")
    print(result.stderr, end="" if result.stderr.endswith("\n") or not result.stderr else "\n")
    print("[e2e] stderr_end")


def run(command: list[str], *, label: str) -> str:
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True)
    _print_command_result(label, command, result, (time.monotonic() - started) * 1000)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed [{label}] ({result.returncode}): {_display_command(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def chat_json_request(
    chat_url: str, path: str, method: str, *, source: str, user_id: str, request_id: str,
    developer: str | None = None, body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{chat_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json", "X-From-Source": source,
        "X-User-Id": user_id, "X-Request-Id": request_id,
    }
    if developer:
        headers["X-Developer"] = developer
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=float(os.getenv("CHAT_API_TIMEOUT_SECONDS", "15"))) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Chat {method} {path} returned HTTP {exc.code}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Chat {method} {path} failed: {exc.reason}") from exc
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Chat {method} {path} returned invalid JSON: {raw}") from exc
    if not isinstance(response, dict) or response.get("code") != 200:
        raise RuntimeError(f"Chat {method} {path} failed: {response}")
    return response


def select_chat_model(payload: dict[str, Any], model_id: str | None = None, provider_id: str | None = None) -> tuple[str, str]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("listAvailableModels returned no data object")
    candidates: list[dict[str, Any]] = []
    for key in ("system_models", "user_models"):
        models = data.get(key, [])
        if not isinstance(models, list):
            raise RuntimeError(f"listAvailableModels returned invalid {key}")
        candidates.extend(model for model in models if isinstance(model, dict))
    matching = [model for model in candidates if model_id is None or model.get("id") == model_id]
    if model_id and not matching:
        raise RuntimeError(f"Requested model_id {model_id!r} is not available to this user")
    for model in matching:
        if not model.get("is_active") or not model.get("support_tools"):
            continue
        mappings = model.get("mappings") or []
        if not isinstance(mappings, list):
            continue
        active = [item for item in mappings if isinstance(item, dict) and item.get("is_active")]
        if provider_id:
            active = [item for item in active if item.get("provider_id") == provider_id]
        if active and isinstance(model.get("id"), str) and isinstance(active[0].get("provider_id"), str):
            return model["id"], active[0]["provider_id"]
    raise RuntimeError("No active tool-capable model mapping is available")


def discover_chat_model(args: argparse.Namespace) -> tuple[str, str]:
    return select_chat_model(chat_json_request(
        args.chat_url, "/chat/model/listAvailableModels", "GET", source=args.source,
        user_id=args.user_id, request_id=f"{args.request_id}-models", developer=args.developer,
    ), args.model_id, args.provider_id)


def create_chat_session(args: argparse.Namespace, title: str) -> str:
    response = chat_json_request(
        args.chat_url, "/chat/session/createSession", "POST", source=args.source,
        user_id=args.user_id, request_id=f"{args.request_id}-session-{title}",
        developer=args.developer, body={"title": title},
    )
    data = response.get("data")
    session_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("createSession returned no session id")
    return session_id


def delete_chat_session(args: argparse.Namespace, session_id: str) -> None:
    chat_json_request(
        args.chat_url, "/chat/session/deleteSession", "POST", source=args.source,
        user_id=args.user_id, request_id=f"{args.request_id}-session-delete-{session_id}",
        developer=args.developer, query={"session_id": session_id},
    )


def metric_running(raw: str) -> int:
    payload = json.loads(raw)
    if payload.get("code") != 200:
        raise RuntimeError(f"pool metrics failed: {payload}")
    return int((payload.get("data") or {}).get("running", 0))


def parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            value = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _event_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def print_sse_summary(events: list[dict[str, Any]]) -> None:
    print("[e2e] sse_summary_begin")
    for event in events:
        if event.get("type") not in {"tool-input-available", "tool-output-available", "finish", "error"}:
            continue
        fields = [f"type={event.get('type')}"]
        if "toolName" in event:
            fields.append(f"toolName={json.dumps(event['toolName'], ensure_ascii=False)}")
        if event.get("type") in {"tool-input-available", "tool-output-available"}:
            fields.append(_event_text(event.get("input") if event.get("type") == "tool-input-available" else event.get("output")))
        print("[e2e] sse_event " + " ".join(fields))
    print("[e2e] sse_summary_end")


class FileTransferFixture:
    """A local HTTP server with deterministic source files and upload capture."""

    def __init__(self, bind_host: str = "0.0.0.0") -> None:
        self.sources = {
            "/source/session-one.txt": b"alpha source for session one\n",
            "/source/session-two.txt": b"bravo source for session two\n",
        }
        self.uploads: dict[str, bytes] = {}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                content = fixture.read_source(self.path)
                if content is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def do_PUT(self) -> None:  # noqa: N802
                if self.path not in {"/upload/session-one.txt", "/upload/session-two.txt"}:
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self.send_error(411)
                    return
                if not fixture.capture_upload(self.path, self.rfile.read(length)):
                    self.send_error(404)
                    return
                self.send_response(201)
                self.end_headers()

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((bind_host, 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FileTransferFixture":
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def url(self, fixture_host: str, path: str) -> str:
        return f"http://{fixture_host}:{self.port}{path}"

    def read_source(self, path: str) -> bytes | None:
        return self.sources.get(path)

    def capture_upload(self, path: str, content: bytes) -> bool:
        if path not in {"/upload/session-one.txt", "/upload/session-two.txt"}:
            return False
        self.uploads[path] = content
        return True

    def assert_uploads(self, cases: list[dict[str, str]]) -> None:
        values: list[bytes] = []
        for case in cases:
            upload_path = case["upload_path"]
            actual = self.uploads.get(upload_path)
            source = self.sources[case["source_path"]].decode("utf-8")
            expected = (source.upper() + f"processed-by={case['name']}\n").encode("utf-8")
            if actual != expected:
                raise RuntimeError(
                    f"Upload {upload_path} did not match expected transformed content: {actual!r}"
                )
            values.append(actual)
        if len(set(values)) != len(values):
            raise RuntimeError("Session uploads are identical; workspace isolation was not demonstrated")


def _download_query(source_url: str, input_file: str, case_name: str, workspace_dir: str) -> str:
    input_path = f"{workspace_dir}/{input_file}"
    output_path = f"{workspace_dir}/output.txt"
    return (
        "在本轮只使用 shell_exec 和 run_sandbox_script。工作区必须使用给定绝对路径，不能使用相对路径或 /home/gem。"
        "先必须用 shell_exec 执行此命令下载文件：\n"
        f"mkdir -p '{workspace_dir}' && curl -fsS -o '{input_path}' -w 'E2E_DOWNLOAD_CURL_MS=%{{time_total}}\\n' '{source_url}'\n"
        "接着必须用 run_sandbox_script，language=python。Python 代码必须读取 "
        f"{input_path}，把内容转成大写后追加一行 processed-by={case_name}，写入 {output_path}。"
        "Python stdout 必须包含 E2E_TRANSFORM_MARKER。完成后简短说明。"
    )


def _upload_query(upload_url: str, marker: str, output_path: str) -> str:
    return (
        "在本轮只使用 shell_exec。必须使用此绝对路径上传 output.txt，不能使用相对路径：\n"
        f"curl -fsS --upload-file '{output_path}' -w '{marker}=%{{time_total}}\\n' '{upload_url}'\n"
        "上传成功后简短说明。"
    )


def _run_chat_turn(args: argparse.Namespace, model_id: str, provider_id: str, session_id: str, label: str, query: str) -> list[dict[str, Any]]:
    command = [
        "bash", str(CHAT_SCRIPT), "--chat-url", args.chat_url, "--source", args.source,
        "--user-id", args.user_id, "--session-id", session_id,
        "--request-id", f"{args.request_id}-{label}", "--model-id", model_id,
        "--provider-id", provider_id,
        *( ["--developer", args.developer] if args.developer else [] ), "--query", query,
    ]
    started = time.monotonic()
    raw = run(command, label=f"chat_{label}")
    print(f"[e2e] chat_turn_duration_ms label={label} duration_ms={(time.monotonic() - started) * 1000:.1f}")
    events = parse_sse(raw)
    print_sse_summary(events)
    return events


def _assert_turn(events: list[dict[str, Any]], *, tools: set[str], input_contains: tuple[str, ...], output_markers: set[str], label: str) -> None:
    if not any(event.get("type") == "finish" for event in events):
        raise RuntimeError(f"Chat SSE did not finish for {label}: {events}")
    calls = [event for event in events if event.get("type") == "tool-input-available"]
    actual_tools = {str(event.get("toolName")) for event in calls}
    missing_tools = tools - actual_tools
    if missing_tools:
        raise RuntimeError(f"Chat did not call {sorted(missing_tools)} for {label}: {calls}")
    inputs = "\n".join(_event_text(event.get("input")) for event in calls)
    missing_inputs = [value for value in input_contains if value not in inputs]
    if missing_inputs:
        raise RuntimeError(f"Expected tool input values {missing_inputs} absent for {label}: {calls}")
    output_text = "\n".join(_event_text(event.get("output")) for event in events if event.get("type") == "tool-output-available")
    missing_markers = {marker for marker in output_markers if marker not in output_text}
    if missing_markers:
        raise RuntimeError(f"Expected output markers {sorted(missing_markers)} absent for {label}: {output_text}")
    for value in _CURL_TIMING_PATTERN.findall(output_text):
        print(f"[e2e] sandbox_curl_duration_ms label={label} duration_ms={value}")
    for value in _SCRIPT_DURATION_PATTERN.findall(output_text):
        print(f"[e2e] sandbox_script_duration_ms label={label} duration_ms={value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-url", default="http://127.0.0.1:19905")
    parser.add_argument("--chat-url", default="http://127.0.0.1:19904")
    parser.add_argument("--source", default="APISIX-wX0iR6tY")
    parser.add_argument("--user-id", default="chat-curl-user")
    parser.add_argument("--request-id", default=f"file-transfer-e2e-{uuid.uuid4().hex}")
    parser.add_argument("--model-id", default=os.getenv("CHAT_MODEL_ID"))
    parser.add_argument("--provider-id", default=os.getenv("CHAT_PROVIDER_ID"))
    parser.add_argument("--developer", default=os.getenv("CHAT_DEVELOPER") or os.getenv("DEVELOPER_NAME"))
    parser.add_argument("--sandbox-fixture-host", required=True, help="Address where sandbox containers can reach this process.")
    parser.add_argument("--fixture-bind-host", default="0.0.0.0")
    parser.add_argument(
        "--container-workspace-root",
        default=os.getenv("SANDBOX_CONTAINER_WORKSPACE_ROOT", "/home/gem/workspaces"),
        help="Container path exported by DockerWorkspaceTransfer.",
    )
    return parser.parse_args()


def main(args: argparse.Namespace | None = None) -> int:
    args = args or parse_args()
    created_sessions: list[str] = []
    primary_error: Exception | None = None
    cases = [
        {"name": "session-one", "source_path": "/source/session-one.txt", "upload_path": "/upload/session-one.txt"},
        {"name": "session-two", "source_path": "/source/session-two.txt", "upload_path": "/upload/session-two.txt"},
    ]
    try:
        model_id, provider_id = discover_chat_model(args)
        print(f"chat_model=PASS model_id={model_id} provider_id={provider_id}")
        for case in cases:
            session_id = create_chat_session(args, f"sandbox-file-transfer-{case['name']}-{args.request_id}")
            case["session_id"] = session_id
            created_sessions.append(session_id)
            print(f"chat_session=PASS case={case['name']} session_id={session_id} user_id={args.user_id}")
        before = metric_running(run(["bash", str(METRICS_SCRIPT), args.sandbox_url], label="pool_metrics_before"))
        with FileTransferFixture(args.fixture_bind_host) as fixture:
            print(f"file_transfer_fixture=PASS port={fixture.port}")
            for case in cases:
                source_url = fixture.url(args.sandbox_fixture_host, case["source_path"])
                upload_url = fixture.url(args.sandbox_fixture_host, case["upload_path"])
                input_file = f"{case['name']}-input.txt"
                download_marker = "E2E_DOWNLOAD_CURL_MS"
                workspace_dir = f"{args.container_workspace_root.rstrip('/')}/{args.user_id}/{case['session_id']}"
                download = _run_chat_turn(args, model_id, provider_id, case["session_id"], f"{case['name']}-download", _download_query(source_url, input_file, case["name"], workspace_dir))
                _assert_turn(download, tools={"shell_exec", "run_sandbox_script"}, input_contains=(source_url, workspace_dir), output_markers={download_marker, "E2E_TRANSFORM_MARKER"}, label=f"{case['name']}-download")
                upload_marker = "E2E_UPLOAD_CURL_MS"
                output_path = f"{workspace_dir}/output.txt"
                upload = _run_chat_turn(args, model_id, provider_id, case["session_id"], f"{case['name']}-upload", _upload_query(upload_url, upload_marker, output_path))
                _assert_turn(upload, tools={"shell_exec"}, input_contains=(upload_url, output_path), output_markers={upload_marker}, label=f"{case['name']}-upload")
            fixture.assert_uploads(cases)
            print("file_transfer_uploads=PASS sessions=2 isolated=true")
        after = metric_running(run(["bash", str(METRICS_SCRIPT), args.sandbox_url], label="pool_metrics_after"))
        if after > before:
            raise RuntimeError(f"Sandbox running count increased after Chat responses: before={before} after={after}")
        print(f"chat_sandbox_file_transfer_e2e=PASS running_before={before} running_after={after}")
    except Exception as exc:
        primary_error = exc
    finally:
        cleanup_errors: list[str] = []
        for session_id in reversed(created_sessions):
            try:
                delete_chat_session(args, session_id)
                print(f"chat_session_cleanup=PASS session_id={session_id}")
            except Exception as cleanup_error:
                cleanup_errors.append(f"{session_id}: {cleanup_error}")
        if cleanup_errors:
            message = "Chat session cleanup failed: " + "; ".join(cleanup_errors)
            if primary_error is not None:
                raise RuntimeError(f"{primary_error}; {message}") from primary_error
            raise RuntimeError(message)
    if primary_error is not None:
        raise primary_error
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"chat_sandbox_file_transfer_e2e=FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
