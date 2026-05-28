from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import json
import os
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

from sandbox.ResultReturn.returnResult import InMemoryExecutionResultRepository, Result
from sandbox.ScriptExecutor.execution.executor_impl import DefaultScriptsExecutor
from sandbox.ScriptExecutor.execution.request_parser import DefaultExecutionRequestParser
from sandbox.ScriptExecutor.package_repo.local_fs_repo import LocalFsScriptPackageRepository
from sandbox.ScriptExecutor.parsers.bat_parser import BatScriptParser
from sandbox.ScriptExecutor.parsers.factory import DefaultScriptParserFactory
from sandbox.ScriptExecutor.parsers.python_parser import PythonScriptParser
from sandbox.ScriptExecutor.scriptExecutor import ExecutionResult
from sandbox.ScriptExecutor.scriptReader import ScriptPackageRepository
from sandbox.service.sandbox_service import DefaultSandboxExecutionService
from sandbox.transport.http.schemas import ExecuteRequestDTO, ExecuteResponseDTO

_DEBUG = (os.getenv("SANDBOX_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(event: str, **fields: Any) -> None:
    if not _DEBUG:
        return
    try:
        payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        payload = str(fields)
    print(f"[SANDBOX][http] {event} | {payload}")


class HttpServer(ABC):
    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError


class StdHttpServer(HttpServer):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        handler_factory: Callable[[], BaseHTTPRequestHandler],
    ) -> None:
        self._host = host
        self._port = port
        self._handler_factory = handler_factory

    def start(self) -> None:
        server = ThreadingHTTPServer((self._host, self._port), self._handler_factory)  # type: ignore[arg-type]
        server.serve_forever()


class SandboxHttpHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        execute_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        get_result_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> None:
        self._execute_fn = execute_fn
        self._get_result_fn = get_result_fn
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        _dbg("http_get", path=self.path)
        if self.path == "/v1/sandbox/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return

        if self.path.startswith("/v1/sandbox/result/"):
            if not self._get_result_fn:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "result lookup is not enabled"})
                return
            request_id = self.path.split("/v1/sandbox/result/", 1)[1].strip()
            if not request_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing request_id"})
                return
            try:
                payload = self._get_result_fn(request_id)
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "result not found"})
                return
            self._send_json(HTTPStatus.OK, payload)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        _dbg("http_post", path=self.path)
        if self.path != "/v1/sandbox/execute":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            raw = self._read_json()
        except ValueError as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        try:
            payload = self._execute_fn(raw)
        except Exception as e:
            _dbg("http_execute_error", error_type=type(e).__name__, error=str(e))
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})
            return
        self._send_json(HTTPStatus.OK, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            raise ValueError("missing request body")
        body = self.rfile.read(length)
        try:
            obj = json.loads(body.decode("utf-8"))
        except Exception:
            raise ValueError("invalid json body")
        if not isinstance(obj, dict):
            raise ValueError("json body must be an object")
        _dbg("http_body_parsed", content_length=length, keys=list(obj.keys()))
        return obj

    def _send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def build_sandbox_http_handler(
    *,
    execute: Callable[[Dict[str, Any]], Dict[str, Any]],
    get_result: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> type[BaseHTTPRequestHandler]:
    def _handler(*args: Any, **kwargs: Any) -> SandboxHttpHandler:
        return SandboxHttpHandler(*args, execute_fn=execute, get_result_fn=get_result, **kwargs)

    return _handler  # type: ignore[return-value]


class SandboxHttpApp:
    def __init__(
        self,
        *,
        package_repo: ScriptPackageRepository,
    ) -> None:
        self._package_repo = package_repo
        self._result_repo = InMemoryExecutionResultRepository()
        self._result = Result(result_repo=self._result_repo)

        parser_factory = DefaultScriptParserFactory()
        parser_factory.register(PythonScriptParser())
        parser_factory.register(BatScriptParser())

        self._request_parser = DefaultExecutionRequestParser(parser_factory=parser_factory)
        self._executor = DefaultScriptsExecutor(parser_factory=parser_factory)
        self._service = DefaultSandboxExecutionService(executor=self._executor, result_return=self._result)

    def execute(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        dto = ExecuteRequestDTO.from_dict(raw)
        if not dto.package_id:
            return {"error": "missing package_id"}

        request_id = str(raw.get("request_id") or raw.get("id") or "").strip() or f"req_{uuid.uuid4().hex}"
        _dbg(
            "execute_begin",
            request_id=request_id,
            package_id=dto.package_id,
            entry=dto.entry,
            args_len=len(dto.args),
            env_keys=list(dto.env.keys()),
        )

        package = self._package_repo.get(dto.package_id)
        _dbg("package_loaded", request_id=request_id, package_id=dto.package_id, file_count=len(package.files))

        structured: Dict[str, Any] = {
            "request_id": request_id,
            "entry": dto.entry,
            "args": dto.args,
            "env": dto.env,
            "timeout_ms": dto.timeout_ms,
            "limits": dto.limits,
        }

        req = self._request_parser.parse(structured, package)
        _dbg(
            "request_parsed",
            request_id=request_id,
            script_type=req.script.script_type.value,
            script_entry=req.script.entry,
            script_args_len=len(req.script.args),
            provider=req.sandbox.provider,
        )
        result = asyncio.run(self._service.execute(req))
        _dbg(
            "execute_end",
            request_id=request_id,
            status=result.status.value,
            sandbox_id=result.sandbox_id,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            stdout_len=len(result.stdout or ""),
            stderr_len=len(result.stderr or ""),
        )
        return self._to_response_dto(result).to_dict()

    def get_result(self, request_id: str) -> Dict[str, Any]:
        result = self._result_repo.get(request_id)
        return self._to_response_dto(result).to_dict()

    def _to_response_dto(self, result: ExecutionResult) -> ExecuteResponseDTO:
        artifacts = []
        if result.artifacts:
            for a in result.artifacts:
                artifacts.append({"name": a.name, "uri": a.uri, "metadata": a.metadata})
        return ExecuteResponseDTO(
            request_id=result.request_id,
            status=result.status.value,
            sandbox_id=result.sandbox_id,
            exit_code=result.exit_code,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            duration_ms=result.duration_ms,
            artifacts=artifacts,
        )


def build_default_http_server(*, host: str, port: int, packages_base_dir: str) -> StdHttpServer:
    repo = LocalFsScriptPackageRepository(packages_base_dir)
    app = SandboxHttpApp(package_repo=repo)
    handler_cls = build_sandbox_http_handler(execute=app.execute, get_result=app.get_result)
    return StdHttpServer(host=host, port=port, handler_factory=handler_cls)


if __name__ == "__main__":
    host = os.getenv("SANDBOX_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = os.getenv("SANDBOX_PORT", "9001").strip() or "9001"
    packages_dir = os.getenv("SANDBOX_PACKAGES_DIR", "./packages").strip() or "./packages"
    server = build_default_http_server(host=host, port=int(port_raw), packages_base_dir=packages_dir)
    server.start()


