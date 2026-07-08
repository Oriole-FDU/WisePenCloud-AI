from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from typing import Optional

from sandbox.ResultReturn.returnResult import Result
from sandbox.ScriptExecutor.scriptExecutor import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    SandboxExecutionService,
    ScriptsExecutor,
 )
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][service]")


class DefaultSandboxExecutionService(SandboxExecutionService):
    def __init__(
        self,
        executor: ScriptsExecutor,
        *,
        result_return: Optional[Result] = None,
        run_in_thread: bool = True,
    ) -> None: # Initialize sandbox execution service
        self._executor = executor
        self._result_return = result_return
        self._run_in_thread = run_in_thread

    async def execute(self, request: ExecutionRequest) -> ExecutionResult: # Execute scripts in sandbox
        started = time.time()
        _dbg(
            "service_execute_begin",
            request_id=request.request_id,
            provider=request.sandbox.provider,
            script_type=request.script.script_type.value,
            entry=request.script.entry,
        )
        try:
            if self._run_in_thread: # Execute scripts in thread
                result = await asyncio.to_thread(self._executor.execute, request)
            else:
                result = self._executor.execute(request)
        except Exception as e:
            duration_ms = int((time.time() - started) * 1000)
            stderr = f"[Sandbox Execution Error]: {type(e).__name__}: {e}"
            _dbg(
                "service_execute_error",
                request_id=request.request_id,
                error_type=type(e).__name__,
                error=str(e),
                duration_ms=duration_ms,
            )
            result = ExecutionResult(
                request_id=request.request_id,
                status=ExecutionStatus.FAILED,
                sandbox_id=None,
                exit_code=None,
                stdout="",
                stderr=stderr,
                duration_ms=duration_ms,
                artifacts=None,
                metadata={"error_type": type(e).__name__},
            )

        if result.duration_ms is None: # Calculate duration if not provided
            duration_ms = int((time.time() - started) * 1000)
            result = replace(result, duration_ms=duration_ms)

        if self._result_return is not None: # Return execution results to chat
            try:
                self._result_return.chatReturn(result)
            except Exception:
                _dbg("result_return_failed", request_id=request.request_id)
                pass
        _dbg(
            "service_execute_end",
            request_id=request.request_id,
            status=result.status.value,
            sandbox_id=result.sandbox_id,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
        )
        return result

