from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from common.logger import log_error

from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool


class RunSandboxScriptTool(BaseTool):
    def __init__(self, *, base_url: str, from_source: Optional[str] = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._from_source = (from_source or "").strip()

    @property
    def name(self) -> str:
        return "run_sandbox_script"

    @property
    def description(self) -> str:
        return (
            "Run a script package in the sandbox environment. "
            "Provide package_id and optional entry/args/env/timeout_ms/limits. "
            "Use this when you need to execute code or scripts and observe stdout/stderr."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package_id": {"type": "string", "minLength": 1},
                "entry": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "timeout_ms": {"type": "integer", "minimum": 1},
                "limits": {"type": "object"},
            },
            "required": ["package_id"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        package_id = str(kwargs.get("package_id") or "").strip()
        if not package_id:
            return "[Tool Error] missing package_id"

        entry = kwargs.get("entry")
        args = kwargs.get("args")
        env = kwargs.get("env")
        timeout_ms = kwargs.get("timeout_ms")
        limits = kwargs.get("limits")

        session_id = str(context.get("session_id") or "").strip()
        user_id = str(context.get("user_id") or "").strip()
        request_id = f"req_{uuid.uuid4().hex}"
        if session_id:
            request_id = f"req_{session_id}_{uuid.uuid4().hex}"

        body: Dict[str, Any] = {
            "request_id": request_id,
            "package_id": package_id,
            "entry": str(entry).strip() if isinstance(entry, str) and entry.strip() else None,
            "args": [str(a) for a in args] if isinstance(args, list) else [],
            "env": {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            "timeout_ms": int(timeout_ms) if isinstance(timeout_ms, int) else None,
            "limits": limits if isinstance(limits, dict) else {},
        }

        url = f"{self._base_url}/v1/sandbox/execute"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Trace-Id": request_id,
        }
        if self._from_source:
            headers["X-From-Source"] = self._from_source
        if user_id:
            headers["X-User-Id"] = user_id
        if session_id:
            headers["X-Session-Id"] = session_id

        try:
            raw = await asyncio.to_thread(self._post_json, url, headers, body)
        except Exception as e:
            log_error("沙箱工具调用失败", e, url=url)
            return f"[Tool Error] sandbox-service request failed: {type(e).__name__}: {e}"

        try:
            payload = json.loads(raw)
        except Exception:
            return "[Tool Error] invalid sandbox response"
        if not isinstance(payload, dict):
            return "[Tool Error] invalid sandbox response"

        if payload.get("error"):
            return self._truncate(f"[Tool Error] {payload.get('error')}")

        text = self._format_result(payload)
        return self._truncate(text)

    def _post_json(self, url: str, headers: Dict[str, str], body: Dict[str, Any]) -> str:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(url=url, method="POST", data=data, headers=headers)
        with urlopen(req, timeout=getattr(settings, "SANDBOX_TIMEOUT_SECONDS", 30)) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _format_result(self, result: Dict[str, Any]) -> str:
        lines = [
            "[Sandbox Execution]",
            f"status: {result.get('status')}",
            f"request_id: {result.get('request_id')}",
            f"sandbox_id: {result.get('sandbox_id')}",
            f"exit_code: {result.get('exit_code')}",
            f"duration_ms: {result.get('duration_ms')}",
            "stdout:",
            str(result.get("stdout") or ""),
            "stderr:",
            str(result.get("stderr") or ""),
        ]
        artifacts = result.get("artifacts")
        if isinstance(artifacts, list) and artifacts:
            lines.append("artifacts:")
            for a in artifacts:
                if not isinstance(a, dict):
                    continue
                name = a.get("name")
                uri = a.get("uri")
                lines.append(f"- name={name} uri={uri}")
        return "\n".join(lines)

    def _truncate(self, text: str) -> str:
        limit = settings.TOOL_RESULT_MAX_CHARS
        if limit and len(text) > limit:
            return text[:limit] + "\n...[truncated]..."
        return text
