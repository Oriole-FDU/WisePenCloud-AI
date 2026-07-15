from __future__ import annotations

from typing import Any, Dict

from sandbox.ScriptExecutor.scriptExecutor import ExecutionRequest, ExecutionRequestParser
from sandbox.ScriptExecutor.scriptExecutor import ExecutionContext, SandboxSpec
from sandbox.ScriptExecutor.scriptReader import ScriptPackage
from sandbox.ScriptExecutor.scriptReader import ScriptParserFactory, ScriptSpec
from sandbox.core.errors import SandboxError, SandboxErrorCode
from sandbox.LifeSpan.sandboxLifespan import SandboxLimits
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][request_parser]")


class DefaultExecutionRequestParser(ExecutionRequestParser):
    def __init__(self, parser_factory: ScriptParserFactory) -> None:
        self._parser_factory = parser_factory

    # 解析执行请求
    # 1. 验证请求 ID
    # 2. 解析上下文
    # 3. 解析沙箱规格
    # 4. 解析脚本包
    def parse(self, structured_request: Dict[str, Any], package: ScriptPackage) -> ExecutionRequest:
        request_id = str(structured_request.get("request_id") or structured_request.get("id") or "").strip()
        if not request_id:
            raise SandboxError(
                code=SandboxErrorCode.VALIDATION_FAILED,
                message="missing request_id",
            )
        _dbg("parse_begin", request_id=request_id, package_id=package.package_id, file_count=len(package.files))

        ctx_raw = structured_request.get("context") if isinstance(structured_request.get("context"), dict) else {}
        context = ExecutionContext(
            session_id=ctx_raw.get("session_id"),
            user_id=ctx_raw.get("user_id"),
            trace_id=ctx_raw.get("trace_id"),
        )

        limits_raw = structured_request.get("limits") if isinstance(structured_request.get("limits"), dict) else {}
        timeout_ms = structured_request.get("timeout_ms")
        if timeout_ms is not None and isinstance(timeout_ms, int):
            limits_raw = {**limits_raw, "timeout_ms": timeout_ms}

        limits = SandboxLimits(
            cpu_cores=limits_raw.get("cpu_cores"),
            memory_mb=limits_raw.get("memory_mb"),
            timeout_ms=limits_raw.get("timeout_ms"),
            disk_mb=limits_raw.get("disk_mb"),
            pids_limit=limits_raw.get("pids_limit"),
            network_enabled=limits_raw.get("network_enabled"),
        )

        sandbox = SandboxSpec(
            provider=str(structured_request.get("provider") or "docker"),
            runtime=structured_request.get("runtime"),
            limits=limits,
            env=structured_request.get("env") if isinstance(structured_request.get("env"), dict) else {},
            metadata=structured_request.get("sandbox_metadata") if isinstance(structured_request.get("sandbox_metadata"), dict) else {},
        )

        parser = self._parser_factory.get_parser(package)
        script = parser.parse(package)
        _dbg(
            "script_parsed",
            request_id=request_id,
            script_type=script.script_type.value,
            entry=script.entry,
            args_len=len(script.args or []),
            env_keys=list((script.env or {}).keys()),
        )

        entry = script.entry
        args = list(script.args or [])
        env = dict(script.env or {})

        entry_override = structured_request.get("entry")
        if isinstance(entry_override, str) and entry_override.strip():
            entry = entry_override.strip()

        args_override = structured_request.get("args")
        if isinstance(args_override, list):
            args = [str(a) for a in args_override]

        env_override = structured_request.get("env")
        if isinstance(env_override, dict):
            env.update({str(k): str(v) for k, v in env_override.items()})

        script = ScriptSpec(
            script_type=script.script_type,
            entry=entry,
            args=args,
            env=env,
            files=script.files,
            working_dir=script.working_dir,
        )
        _dbg(
            "parse_end",
            request_id=request_id,
            provider=sandbox.provider,
            timeout_ms=sandbox.limits.timeout_ms,
            entry=script.entry,
            args_len=len(script.args),
        )

        return ExecutionRequest(
            request_id=request_id,
            context=context,
            sandbox=sandbox,
            script=script,
            script_package=package,
            result_sink=None,
            metadata=structured_request.get("metadata") if isinstance(structured_request.get("metadata"), dict) else {},
        )
