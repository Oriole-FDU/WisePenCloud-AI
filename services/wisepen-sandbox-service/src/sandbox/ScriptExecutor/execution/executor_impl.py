from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import List, Optional

from sandbox.LifeSpan.factory.sandbox_factory import DefaultSandboxFactory
from sandbox.LifeSpan.sandboxLifespan import Sandbox, SandboxCreateRequest
from sandbox.ScriptExecutor.execution.runners.bat_runner import BatRunner
from sandbox.ScriptExecutor.execution.runners.python_runner import PythonRunner
from sandbox.ScriptExecutor.execution.runners.base import Runner
from sandbox.ScriptExecutor.scriptExecutor import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    ScriptsExecutor,
)
from sandbox.ScriptExecutor.scriptReader import ScriptFile, ScriptPackage, ScriptSpec, ScriptType
from sandbox.core.errors import SandboxError, SandboxErrorCode
from sandbox.ResultReturn.formatters.tool_text_formatter import DefaultToolTextFormatter, ToolTextFormatter
from sandbox.ScriptExecutor.scriptReader import ScriptParserFactory
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][executor]")


class DefaultScriptsExecutor(ScriptsExecutor):
    def __init__(
        self,
        *,
        parser_factory: Optional[ScriptParserFactory] = None,
        formatter: Optional[ToolTextFormatter] = None,
    ) -> None:
        super().__init__()
        self._parser_factory = parser_factory
        self._formatter = formatter or DefaultToolTextFormatter()

    def Execute(self, request: ExecutionRequest) -> ExecutionResult:
        started = time.time()
        sandbox = self._get_sandbox(request)
        container_id: Optional[str] = None
        staging_dir: Optional[str] = None
        _dbg(
            "execute_begin",
            request_id=request.request_id,
            provider=request.sandbox.provider,
            script_type=request.script.script_type.value,
            entry=request.script.entry,
            args_len=len(request.script.args),
            file_count=len(request.script_package.files),
        )

        try:
            staging_dir = self._materialize_package(request.script_package, request.request_id)
            _dbg("staging_ready", request_id=request.request_id, staging_dir=staging_dir)
            create_req = SandboxCreateRequest(
                request_id=request.request_id,
                limits=request.sandbox.limits,
                runtime=request.sandbox.runtime,
                env=request.sandbox.env,
                metadata={
                    **(request.sandbox.metadata or {}),
                    "image": request.sandbox.runtime or ((request.sandbox.metadata or {}).get("image")),
                    "workdir": "/workspace",
                },
            )
            info = sandbox.createSandbox(create_req)
            container_id = info.sandbox_id
            _dbg("sandbox_created", request_id=request.request_id, sandbox_id=container_id, state=info.state.value)

            self._docker_cp_dir(staging_dir, container_id, "/workspace")
            _dbg("docker_cp_done", request_id=request.request_id, sandbox_id=container_id)

            runner = self._select_runner(request.script)
            cmd = runner.build_command(request.script, request.sandbox)
            _dbg(
                "runner_selected",
                request_id=request.request_id,
                sandbox_id=container_id,
                workdir=self._container_workdir(request.script.working_dir),
                cmd=cmd,
                timeout_ms=request.sandbox.limits.timeout_ms,
            )
            exit_code, stdout, stderr = self._docker_exec(
                container_id=container_id,
                workdir=self._container_workdir(request.script.working_dir),
                cmd=cmd,
                timeout_ms=request.sandbox.limits.timeout_ms,
            )

            duration_ms = int((time.time() - started) * 1000)
            status = ExecutionStatus.SUCCEEDED if exit_code == 0 else ExecutionStatus.FAILED
            _dbg(
                "execute_finished",
                request_id=request.request_id,
                sandbox_id=container_id,
                exit_code=exit_code,
                status=status.value,
                duration_ms=duration_ms,
                stdout_len=len(stdout or ""),
                stderr_len=len(stderr or ""),
            )
            return ExecutionResult(
                request_id=request.request_id,
                status=status,
                sandbox_id=container_id,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                artifacts=None,
                metadata=None,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - started) * 1000)
            _dbg(
                "execute_timeout",
                request_id=request.request_id,
                sandbox_id=container_id,
                duration_ms=duration_ms,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status=ExecutionStatus.TIMEOUT,
                sandbox_id=container_id,
                exit_code=None,
                stdout="",
                stderr="[Sandbox Execution Error]: timeout",
                duration_ms=duration_ms,
                artifacts=None,
                metadata=None,
            )
        except SandboxError as e:
            duration_ms = int((time.time() - started) * 1000)
            _dbg(
                "execute_sandbox_error",
                request_id=request.request_id,
                sandbox_id=container_id,
                code=e.code.value,
                message=e.message,
                detail=e.detail,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status=ExecutionStatus.FAILED,
                sandbox_id=container_id,
                exit_code=None,
                stdout="",
                stderr=f"[Sandbox Execution Error]: {e.code.value}: {e.message}",
                duration_ms=duration_ms,
                artifacts=None,
                metadata={"detail": e.detail} if e.detail else None,
            )
        except Exception as e:
            duration_ms = int((time.time() - started) * 1000)
            _dbg(
                "execute_exception",
                request_id=request.request_id,
                sandbox_id=container_id,
                error_type=type(e).__name__,
                error=str(e),
            )
            return ExecutionResult(
                request_id=request.request_id,
                status=ExecutionStatus.FAILED,
                sandbox_id=container_id,
                exit_code=None,
                stdout="",
                stderr=f"[Sandbox Execution Error]: {type(e).__name__}: {e}",
                duration_ms=duration_ms,
                artifacts=None,
                metadata=None,
            )
        finally:
            if container_id:
                try:
                    sandbox.removeSandbox(container_id)
                    _dbg("sandbox_removed", request_id=request.request_id, sandbox_id=container_id)
                except Exception:
                    _dbg("sandbox_remove_failed", request_id=request.request_id, sandbox_id=container_id)
                    pass
            if staging_dir:
                shutil.rmtree(staging_dir, ignore_errors=True)
                _dbg("staging_removed", request_id=request.request_id, staging_dir=staging_dir)

    def getInputScript(self, package: ScriptPackage) -> ScriptSpec:
        if self._parser_factory is None:
            raise SandboxError(
                code=SandboxErrorCode.INTERNAL_ERROR,
                message="ScriptParserFactory is not configured",
            )
        parser = self._parser_factory.get_parser(package)
        return parser.parse(package)

    def outputResult(self, result: ExecutionResult) -> str:
        return self._formatter.format(result)

    def _get_sandbox(self, request: ExecutionRequest) -> Sandbox:
        factory = DefaultSandboxFactory()
        return Sandbox(factory=factory, provider_name=request.sandbox.provider or "docker")

    def _materialize_package(self, package: ScriptPackage, request_id: str) -> str:
        base = tempfile.mkdtemp(prefix=f"wisepen-sandbox-{request_id}-")
        for f in package.files:
            self._write_file(base, f)
        return base

    def _write_file(self, base: str, f: ScriptFile) -> None:
        rel = f.file_name.replace("\\", "/")
        if rel.startswith("/") or ".." in rel.split("/"):
            raise SandboxError(
                code=SandboxErrorCode.VALIDATION_FAILED,
                message="invalid file path in script package",
                detail=rel,
            )
        abs_path = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as fp:
            fp.write(f.content)

    def _select_runner(self, script: ScriptSpec) -> Runner:
        if script.script_type == ScriptType.PYTHON:
            return PythonRunner()
        if script.script_type == ScriptType.BAT:
            return BatRunner()
        raise SandboxError(
            code=SandboxErrorCode.UNSUPPORTED_SCRIPT,
            message=f"unsupported script type: {script.script_type.value}",
        )

    def _container_workdir(self, working_dir: str) -> str:
        wd = (working_dir or ".").replace("\\", "/").strip()
        if not wd or wd == ".":
            return "/workspace"
        if wd.startswith("/") or ".." in wd.split("/"):
            return "/workspace"
        return f"/workspace/{wd}"

    def _docker_cp_dir(self, local_dir: str, container_id: str, container_dir: str) -> None:
        src = os.path.join(local_dir, ".")
        self._run_docker(["cp", src, f"{container_id}:{container_dir}"])

    def _docker_exec(
        self,
        *,
        container_id: str,
        workdir: str,
        cmd: List[str],
        timeout_ms: Optional[int],
    ) -> tuple[int, str, str]:
        args = ["exec", "-w", workdir, container_id, *cmd]
        timeout_s = (timeout_ms / 1000) if timeout_ms and timeout_ms > 0 else None
        completed = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return int(completed.returncode), completed.stdout or "", completed.stderr or ""

    def _run_docker(self, args: List[str]) -> str:
        completed = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise SandboxError(
                code=SandboxErrorCode.SANDBOX_PROVIDER_ERROR,
                message=f"docker command failed: {' '.join(args[:2])}",
                detail=detail[:2000] if detail else None,
            )
        return (completed.stdout or "").strip()
