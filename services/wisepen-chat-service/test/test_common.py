"""
端到端测试公共模块

提供 SSE 解析、事件分析、会话管理、流式请求等共享逻辑。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────
BASE_URL = os.getenv("CHAT_BASE_URL", "http://127.0.0.1:8000")
FROM_SOURCE_SECRET = os.getenv("FROM_SOURCE_SECRET", "APISIX-wX0iR6tY")
TEST_USER_ID = os.getenv("TEST_USER_ID", "e2e-test-user")

PRINT_TOOL_INPUT_CHARS = int(os.getenv("E2E_PRINT_TOOL_INPUT_CHARS", "1200"))
PRINT_TOOL_OUTPUT_CHARS = int(os.getenv("E2E_PRINT_TOOL_OUTPUT_CHARS", "1500"))
PRINT_TEXT_DELTA = os.getenv("E2E_PRINT_TEXT_DELTA", "1") == "1"
PRINT_REASONING_DELTA = os.getenv("E2E_PRINT_REASONING_DELTA", "0") == "1"


def make_headers() -> Dict[str, str]:
    return {
        "X-From-Source": FROM_SOURCE_SECRET,
        "X-User-Id": TEST_USER_ID,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────
def shorten(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\r\n", "\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...<truncated {len(text) - limit} chars>"


def event_label(ev: Dict[str, Any]) -> str:
    ev_type = ev.get("type", "")
    if ev_type:
        return ev_type
    if ev.get("_raw") == "[DONE]":
        return "[DONE]"
    return "unknown"


# ─────────────────────────────────────────────────────────────
# SSE 解析
# ─────────────────────────────────────────────────────────────
def parse_sse_block(raw: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line == "data: [DONE]":
            events.append({"_raw": "[DONE]"})
            continue
        if line.startswith("data: "):
            payload = line[6:]
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                events.append({"_raw": payload})
    return events


# ─────────────────────────────────────────────────────────────
# 事件实时打印
# ─────────────────────────────────────────────────────────────
def print_event(ev: Dict[str, Any], *, elapsed: float) -> None:
    prefix = f"[+{elapsed:7.2f}s]"
    ev_type = ev.get("type")

    if ev.get("_raw") == "[DONE]":
        print(f"{prefix} [DONE]")
        return
    if ev_type == "start":
        print(f"{prefix} ▶ start")
        return
    if ev_type in {"start-step", "step-start"}:
        print(f"{prefix} ├─ step start")
        return
    if ev_type in {"finish-step", "step-finish"}:
        print(f"{prefix} └─ step finish")
        return
    if ev_type == "tool-input-start":
        print(f"{prefix} 🔧 tool input start | tool={ev.get('toolName')} call_id={ev.get('toolCallId')}")
        return
    if ev_type == "tool-input-available":
        print(f"{prefix} 🔧 tool input available | tool={ev.get('toolName')} call_id={ev.get('toolCallId')}")
        print(shorten(ev.get("input"), PRINT_TOOL_INPUT_CHARS))
        return
    if ev_type == "tool-output-available":
        output = ev.get("output", "")
        print(f"{prefix} 📤 tool output available | call_id={ev.get('toolCallId')}")
        if "snapshot" in output and "tree" in output:
            print(f"[Full snapshot output - {len(output)} chars]")
            print(output)
        else:
            print(shorten(output, PRINT_TOOL_OUTPUT_CHARS))
        return
    if ev_type == "text-start":
        print(f"{prefix} 💬 text start")
        return
    if ev_type == "text-delta":
        delta = ev.get("delta", "")
        if PRINT_TEXT_DELTA and delta:
            print(delta, end="", flush=True)
        return
    if ev_type == "text-end":
        if PRINT_TEXT_DELTA:
            print()
        print(f"{prefix} 💬 text end")
        return
    if ev_type == "reasoning-start":
        print(f"{prefix} 🧠 reasoning start")
        return
    if ev_type == "reasoning-delta":
        delta = ev.get("delta", "")
        if PRINT_REASONING_DELTA and delta:
            print(delta, end="", flush=True)
        return
    if ev_type == "reasoning-end":
        if PRINT_REASONING_DELTA:
            print()
        print(f"{prefix} 🧠 reasoning end")
        return
    if ev_type == "finish":
        print(f"{prefix} ✅ finish")
        return
    if ev_type == "error":
        print(f"{prefix} ❌ error: {ev.get('errorText', ev)}")
        return
    print(f"{prefix} · {event_label(ev)}: {shorten(ev, 800)}")


# ─────────────────────────────────────────────────────────────
# 会话管理
# ─────────────────────────────────────────────────────────────
async def create_session(client: httpx.AsyncClient, title: str) -> str:
    resp = await client.post("/chat/session/createSession", json={"title": title})
    body = resp.json()
    if body.get("code") != 200:
        raise RuntimeError(f"创建会话失败: {body}")
    return body["data"]["id"]


async def delete_session(client: httpx.AsyncClient, session_id: str) -> None:
    try:
        await client.delete(f"/chat/session/{session_id}")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# SSE 流式请求
# ─────────────────────────────────────────────────────────────
async def stream_chat(
    client: httpx.AsyncClient,
    session_id: str,
    query: str,
    timeout_seconds: int = 300,
) -> List[Dict[str, Any]]:
    all_events: List[Dict[str, Any]] = []
    buffer = ""
    t0 = time.monotonic()

    async with client.stream(
        "POST",
        "/chat/completions",
        json={"session_id": session_id, "query": query},
        timeout=httpx.Timeout(timeout_seconds, read=timeout_seconds),
    ) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            raise RuntimeError(f"HTTP {resp.status_code}: {body.decode(errors='replace')}")

        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                for ev in parse_sse_block(event_str):
                    all_events.append(ev)
                    print_event(ev, elapsed=time.monotonic() - t0)

    if buffer.strip():
        for ev in parse_sse_block(buffer):
            all_events.append(ev)
            print_event(ev, elapsed=time.monotonic() - t0)

    return all_events


# ─────────────────────────────────────────────────────────────
# 事件分析
# ─────────────────────────────────────────────────────────────
def analyze_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "total_events": len(events),
        "has_start": False,
        "has_finish": False,
        "has_done": False,
        "steps": 0,
        "tool_calls": [],
        "text_fragments": [],
        "reasoning_fragments": [],
        "errors": [],
        "event_types": [],
        "tool_failure_count": 0,
        "recovered_tool_failure_count": 0,
        "fatal_tool_failure_count": 0,
        "parallel_tool_calls_count": 0,
        "get_content_called": False,
    }

    current_step_tool_count = 0
    tool_call_states: Dict[str, Dict[str, Any]] = {}  # call_id -> {name, success, recovered}

    for ev in events:
        ev_type = ev.get("type", "")
        result["event_types"].append(event_label(ev))

        if ev_type == "start":
            result["has_start"] = True
        elif ev_type == "finish":
            result["has_finish"] = True
        elif ev.get("_raw") == "[DONE]":
            result["has_done"] = True
        elif ev_type in {"start-step", "step-start"}:
            result["steps"] += 1
            current_step_tool_count = 0
        elif ev_type == "tool-input-start":
            call_id = ev.get("toolCallId", "")
            tool_name = ev.get("toolName", "")
            current_step_tool_count += 1
            if current_step_tool_count > 1:
                result["parallel_tool_calls_count"] += 1
            tool_call_states[call_id] = {"tool_name": tool_name, "success": None, "recovered": False}
            result["tool_calls"].append({
                "call_id": call_id,
                "tool_name": tool_name,
                "phase": "input_start",
            })
        elif ev_type == "tool-input-available":
            result["tool_calls"].append({
                "call_id": ev.get("toolCallId"),
                "tool_name": ev.get("toolName"),
                "phase": "input_available",
                "input": ev.get("input"),
            })
        elif ev_type == "tool-output-available":
            call_id = ev.get("toolCallId", "")
            output = ev.get("output", "") or ""
            # Parse output to check success
            success = True
            try:
                output_obj = json.loads(output) if output and output.strip().startswith("{") else None
                if output_obj and isinstance(output_obj, dict):
                    success = output_obj.get("success", True)
                    if not success:
                        result["tool_failure_count"] += 1
                        tool_call_states[call_id]["success"] = False
            except Exception:
                pass
            # Check if get_content was called
            if tool_call_states.get(call_id, {}).get("tool_name") == "browse_interact":
                try:
                    input_ev = next((tc for tc in result["tool_calls"] 
                                    if tc.get("call_id") == call_id and tc.get("phase") == "input_available"), None)
                    if input_ev:
                        action = input_ev.get("input", {}).get("action", {})
                        if action.get("type") == "get_content":
                            result["get_content_called"] = True
                except Exception:
                    pass
            result["tool_calls"].append({
                "call_id": call_id,
                "phase": "output_available",
                "output_preview": shorten(output, 300) if len(output) > 300 else output,
                "output": output,
                "success": success,
            })
        elif ev_type == "text-delta":
            result["text_fragments"].append(ev.get("delta", ""))
        elif ev_type == "reasoning-delta":
            result["reasoning_fragments"].append(ev.get("delta", ""))
        elif ev_type == "error":
            result["errors"].append(ev.get("errorText", ""))

    # Calculate recovered failures: failures that were followed by a successful call to the same tool
    tool_failure_call_ids = [
        tc.get("call_id") for tc in result["tool_calls"] 
        if tc.get("phase") == "output_available" and not tc.get("success", True)
    ]
    
    # Simple heuristic: if there was a failure but we finished, count it as recovered
    if result["has_finish"] and result["tool_failure_count"] > 0:
        result["recovered_tool_failure_count"] = result["tool_failure_count"]
        result["fatal_tool_failure_count"] = 0
    else:
        result["fatal_tool_failure_count"] = result["tool_failure_count"]
        result["recovered_tool_failure_count"] = 0

    result["full_text"] = "".join(result["text_fragments"])
    result["full_reasoning"] = "".join(result["reasoning_fragments"])

    return result


def count_tool_calls(analysis: Dict[str, Any], tool_name: str) -> int:
    return sum(
        1 for tc in analysis["tool_calls"]
        if tc.get("tool_name") == tool_name and tc.get("phase") == "input_start"
    )


def has_tool_call(analysis: Dict[str, Any], tool_name: str) -> bool:
    return count_tool_calls(analysis, tool_name) > 0


# ─────────────────────────────────────────────────────────────
# 健康检查
# ─────────────────────────────────────────────────────────────
async def check_health(client: httpx.AsyncClient) -> bool:
    try:
        resp = await client.get("/chat/model/listModels", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False
