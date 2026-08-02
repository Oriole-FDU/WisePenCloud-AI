#!/usr/bin/env python3
"""Run the live Chat -> MCP -> Sandbox path using the curl probes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
SANDBOX_SCRIPT = ROOT / "wisepen-sandbox-service" / "scripts" / "check_sandbox_mcp.sh"
METRICS_SCRIPT = ROOT / "wisepen-sandbox-service" / "scripts" / "check_pool_metrics.sh"
CHAT_SCRIPT = ROOT / "wisepen-chat-service" / "scripts" / "run_chat_request.sh"


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
    label: str,
    command: list[str],
    result: subprocess.CompletedProcess[str],
    duration_ms: float,
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
    chat_url: str,
    path: str,
    method: str,
    *,
    source: str,
    user_id: str,
    request_id: str,
    developer: str | None = None,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call a Chat JSON endpoint with the trusted gateway context headers."""
    url = f"{chat_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json",
        "X-From-Source": source,
        "X-User-Id": user_id,
        "X-Request-Id": request_id,
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
    if not isinstance(response, dict):
        raise RuntimeError(f"Chat {method} {path} returned an invalid response: {response!r}")
    if response.get("code") != 200:
        raise RuntimeError(
            f"Chat {method} {path} failed: code={response.get('code')} msg={response.get('msg')}"
        )
    return response


def select_chat_model(
    payload: dict[str, Any],
    model_id: str | None = None,
    provider_id: str | None = None,
) -> tuple[str, str]:
    """Select a tool-capable model and a concrete active provider mapping."""
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("listAvailableModels returned no data object")

    candidates: list[dict[str, Any]] = []
    for key in ("system_models", "user_models"):
        models = data.get(key, [])
        if not isinstance(models, list):
            raise RuntimeError(f"listAvailableModels returned invalid {key}")
        candidates.extend(model for model in models if isinstance(model, dict))

    matching_models = [model for model in candidates if model_id is None or model.get("id") == model_id]
    if model_id and not matching_models:
        raise RuntimeError(f"Requested model_id {model_id!r} is not available to this user")

    for model in matching_models:
        if not model.get("is_active") or not model.get("support_tools"):
            continue
        mappings = model.get("mappings") or []
        if not isinstance(mappings, list):
            continue
        active_mappings = [mapping for mapping in mappings if isinstance(mapping, dict) and mapping.get("is_active")]
        if provider_id:
            active_mappings = [mapping for mapping in active_mappings if mapping.get("provider_id") == provider_id]
        if active_mappings:
            selected_model_id = model.get("id")
            selected_provider_id = active_mappings[0].get("provider_id")
            if isinstance(selected_model_id, str) and selected_model_id and isinstance(selected_provider_id, str) and selected_provider_id:
                return selected_model_id, selected_provider_id

    requested = f" for model_id {model_id!r}" if model_id else ""
    if provider_id:
        requested += f" and provider_id {provider_id!r}"
    raise RuntimeError(f"No active tool-capable model mapping is available{requested}")


def discover_chat_model(args: argparse.Namespace) -> tuple[str, str]:
    response = chat_json_request(
        args.chat_url,
        "/chat/model/listAvailableModels",
        "GET",
        source=args.source,
        user_id=args.user_id,
        request_id=f"{args.request_id}-models",
        developer=args.developer,
    )
    return select_chat_model(response, args.model_id, args.provider_id)


def create_chat_session(args: argparse.Namespace) -> str:
    response = chat_json_request(
        args.chat_url,
        "/chat/session/createSession",
        "POST",
        source=args.source,
        user_id=args.user_id,
        request_id=f"{args.request_id}-session-create",
        developer=args.developer,
        body={"title": f"sandbox-e2e-{args.request_id}"},
    )
    data = response.get("data")
    session_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("createSession returned no session id")
    return session_id


def delete_chat_session(args: argparse.Namespace, session_id: str) -> None:
    chat_json_request(
        args.chat_url,
        "/chat/session/deleteSession",
        "POST",
        source=args.source,
        user_id=args.user_id,
        request_id=f"{args.request_id}-session-delete",
        developer=args.developer,
        query={"session_id": session_id},
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


def _event_output_summary(output: Any) -> str:
    values = find_json_values(output)
    if not values:
        return f"output={json.dumps(output, ensure_ascii=False, default=str)}"
    fields = []
    for key in ("status", "request_id", "sandbox_id", "exit_code", "stdout", "stderr"):
        present = [value.get(key) for value in values if key in value]
        if present:
            fields.append(f"{key}={json.dumps(present[-1], ensure_ascii=False, default=str)}")
    return " ".join(fields) or f"output={json.dumps(output, ensure_ascii=False, default=str)}"


def print_sse_summary(events: list[dict[str, Any]]) -> None:
    print("[e2e] sse_summary_begin")
    for event in events:
        event_type = event.get("type")
        if event_type not in {
            "start", "start-step", "tool-input-start", "tool-input-available",
            "tool-output-available", "finish-step", "finish", "error",
        }:
            continue
        fields = [f"type={event_type}"]
        for key in ("toolName", "toolCallId", "errorText"):
            if key in event:
                fields.append(f"{key}={json.dumps(event[key], ensure_ascii=False, default=str)}")
        if event_type == "tool-input-available":
            fields.append(f"input={json.dumps(event.get('input'), ensure_ascii=False, default=str)}")
        elif event_type == "tool-output-available":
            fields.append(_event_output_summary(event.get("output")))
        print("[e2e] sse_event " + " ".join(fields))
    print("[e2e] sse_summary_end")


def find_json_values(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "status" in value and ("request_id" in value or "sandbox_id" in value):
            found.append(value)
        for child in value.values():
            found.extend(find_json_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_json_values(child))
    elif isinstance(value, str):
        try:
            found.extend(find_json_values(json.loads(value)))
        except (TypeError, json.JSONDecodeError):
            pass
    return found


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-url", default="http://127.0.0.1:19905")
    parser.add_argument("--chat-url", default="http://127.0.0.1:19904")
    parser.add_argument("--source", default="APISIX-wX0iR6tY")
    parser.add_argument("--user-id", default="chat-curl-user")
    parser.add_argument("--session-id", help="Reuse an existing session instead of creating a temporary one.")
    parser.add_argument("--request-id", default=f"e2e-{uuid.uuid4().hex}")
    parser.add_argument("--model-id", default=os.getenv("CHAT_MODEL_ID"))
    parser.add_argument("--provider-id", default=os.getenv("CHAT_PROVIDER_ID"))
    parser.add_argument("--developer", default=os.getenv("CHAT_DEVELOPER") or os.getenv("DEVELOPER_NAME"))
    parser.add_argument(
        "--query",
        default="请使用 run_sandbox_script 执行 Python 代码 print('chat-mcp-curl-e2e')，并返回 JSON 执行结果。",
    )
    return parser.parse_args()


def main(args: argparse.Namespace | None = None) -> int:
    args = args or parse_args()
    session_id = args.session_id
    created_session = False
    primary_error: Exception | None = None

    try:
        model_id, provider_id = discover_chat_model(args)
        print(f"chat_model=PASS model_id={model_id} provider_id={provider_id}")
        if not session_id:
            session_id = create_chat_session(args)
            created_session = True
            print(f"chat_session=PASS session_id={session_id}")

        run([
            "bash", str(SANDBOX_SCRIPT),
            "--base-url", args.sandbox_url,
            "--source", args.source,
            "--user-id", args.user_id,
            "--session-id", session_id,
            "--request-id", f"{args.request_id}-mcp",
        ], label="sandbox_mcp_probe")

        before = metric_running(run(["bash", str(METRICS_SCRIPT), args.sandbox_url], label="pool_metrics_before"))
        chat_sse = run([
            "bash", str(CHAT_SCRIPT),
            "--chat-url", args.chat_url,
            "--source", args.source,
            "--user-id", args.user_id,
            "--session-id", session_id,
            "--request-id", args.request_id,
            "--model-id", model_id,
            "--provider-id", provider_id,
            *(["--developer", args.developer] if args.developer else []),
            "--query", args.query,
        ], label="chat_sse_request")
        print("[e2e] chat_sse_raw_begin")
        print(chat_sse, end="" if chat_sse.endswith("\n") else "\n")
        print("[e2e] chat_sse_raw_end")
        events = parse_sse(chat_sse)
        print_sse_summary(events)
        tool_calls = [event for event in events if event.get("type") == "tool-input-available"]
        tool_outputs = [event for event in events if event.get("type") == "tool-output-available"]
        if not any(event.get("toolName") == "run_sandbox_script" for event in tool_calls):
            raise RuntimeError(f"Chat did not call run_sandbox_script; events={events}")
        if not any("chat-mcp-curl-e2e" in json.dumps(event, ensure_ascii=False) for event in tool_outputs):
            raise RuntimeError(f"Sandbox marker missing from Chat tool output: {tool_outputs}")
        execution_results = find_json_values(tool_outputs)
        if not any(item.get("status") in {"succeeded", "completed"} for item in execution_results):
            raise RuntimeError(f"No successful JSON sandbox result in Chat SSE: {tool_outputs}")
        if not any(event.get("type") == "finish" for event in events):
            raise RuntimeError(f"Chat SSE did not finish: {events}")

        after = metric_running(run(["bash", str(METRICS_SCRIPT), args.sandbox_url], label="pool_metrics_after"))
        if after > before:
            raise RuntimeError(
                f"Sandbox running count increased after Chat response: before={before} after={after}"
            )
        print(f"chat_mcp_sandbox_e2e=PASS running_before={before} running_after={after}")
    except Exception as exc:
        primary_error = exc
    finally:
        if created_session and session_id:
            try:
                delete_chat_session(args, session_id)
                print(f"chat_session_cleanup=PASS session_id={session_id}")
            except Exception as cleanup_error:
                message = f"Chat session cleanup failed for {session_id}: {cleanup_error}"
                if primary_error is not None:
                    raise RuntimeError(f"{primary_error}; {message}") from primary_error
                raise RuntimeError(message) from cleanup_error

    if primary_error is not None:
        raise primary_error
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"chat_mcp_sandbox_e2e=FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
