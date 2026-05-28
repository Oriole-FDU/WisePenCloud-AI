# Return execution results of scripts to chat
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from threading import Lock
from typing import Dict, Optional

from sandbox.LifeSpan.sandboxLifespan import Sandbox as LifeSpanSandbox
from sandbox.LifeSpan.sandboxLifespan import SandboxInfo
from sandbox.ResultReturn.formatters.tool_text_formatter import DefaultToolTextFormatter, ToolTextFormatter
from sandbox.ScriptExecutor.scriptExecutor import ExecutionResult

_DEBUG = (os.getenv("SANDBOX_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(event: str, **fields: object) -> None:
    if not _DEBUG:
        return
    try:
        payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        payload = str(fields)
    print(f"[SANDBOX][result] {event} | {payload}")


class ResultSinkAdapter(ABC):
    @abstractmethod
    def send(self, result: ExecutionResult) -> None: # Send execution results to sink
        raise NotImplementedError


class ExecutionResultRepository(ABC):
    @abstractmethod
    def save(self, result: ExecutionResult) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, request_id: str) -> ExecutionResult:
        raise NotImplementedError


class InMemoryExecutionResultRepository(ExecutionResultRepository):
    def __init__(self) -> None:
        self._lock = Lock()
        self._data: Dict[str, ExecutionResult] = {}

    def save(self, result: ExecutionResult) -> None:
        with self._lock:
            self._data[result.request_id] = result
        _dbg("save", request_id=result.request_id, status=result.status.value, sandbox_id=result.sandbox_id)

    def get(self, request_id: str) -> ExecutionResult:
        with self._lock:
            if request_id not in self._data:
                _dbg("get_not_found", request_id=request_id)
                raise KeyError(f"ExecutionResult '{request_id}' not found.")
            result = self._data[request_id]
        _dbg("get", request_id=request_id, status=result.status.value)
        return result


class Result:
    def __init__(
        self,
        sink: Optional[ResultSinkAdapter] = None,
        *,
        formatter: Optional[ToolTextFormatter] = None,
        sandbox: Optional[LifeSpanSandbox] = None,
        result_repo: Optional[ExecutionResultRepository] = None,
    ) -> None:
        self._sink = sink
        self._formatter = formatter or DefaultToolTextFormatter()
        self._sandbox = sandbox
        self._repo = result_repo or InMemoryExecutionResultRepository()

    def getSandbox(self, sandbox_id: str) -> SandboxInfo: # Get sandbox info from sandbox
        if not self._sandbox:
            raise RuntimeError("Sandbox dependency is not configured.")
        _dbg("get_sandbox", sandbox_id=sandbox_id)
        return self._sandbox.getSandboxInfo(sandbox_id)

    def getSandboxResult(self, request_id: str) -> ExecutionResult: # Get execution results from repository
        _dbg("get_result", request_id=request_id)
        return self._repo.get(request_id)

    def chatReturn(self, result: ExecutionResult) -> str: # Return execution results to chat
        _dbg("chat_return_begin", request_id=result.request_id, status=result.status.value)
        self._repo.save(result)
        if self._sink is not None:
            try:
                self._sink.send(result)
            except Exception:
                _dbg("sink_send_failed", request_id=result.request_id)
                pass
        text = self._formatter.format(result)
        _dbg("chat_return_end", request_id=result.request_id, text_len=len(text))
        return text
