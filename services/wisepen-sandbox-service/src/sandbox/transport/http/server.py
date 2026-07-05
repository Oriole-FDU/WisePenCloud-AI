from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import json
import os
import subprocess
import tempfile
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
from sandbox.Queue.container_queue import ContainerQueue
from sandbox.Queue.file_manager import FileManager
from sandbox.Queue.watcher import Watcher

_DEBUG = (os.getenv("SANDBOX_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(event: str, **fields: Any) -> None:
    if not _DEBUG:
        return
    try:
        payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        payload = str(fields)
    print(f"[SANDBOX][http] {event} | {payload}")


# ---- HTTP Server infrastructure ----

class HttpServer(ABC):
    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError


class StdHttpServer(HttpServer):
    def __init__(self, *, host: str, port: int,
                 handler_factory: Callable[[], BaseHTTPRequestHandler]) -> None:
        self._host = host
        self._port = port
        self._handler_factory = handler_factory

    def start(self) -> None:
        server = ThreadingHTTPServer((self._host, self._port), self._handler_factory)  # type: ignore[arg-type]
        server.serve_forever()


# ---- Request Handler ----

class SandboxHttpHandler(BaseHTTPRequestHandler):
    """Dispatches HTTP requests to SandboxHttpApp methods."""

    def __init__(self, *args: Any, app: "SandboxHttpApp", **kwargs: Any) -> None:
        self._app = app
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        _dbg("http_get", path=self.path)

        if self.path == "/v1/sandbox/health":
            self._handle(lambda: self._app.health_check())
            return

        if self.path.startswith("/v1/sandbox/result/"):
            request_id = self.path.split("/v1/sandbox/result/", 1)[1].strip()
            if not request_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing request_id"})
                return
            self._handle(lambda: self._app.get_result(request_id))
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        _dbg("http_post", path=self.path)
        try:
            raw = self._read_json()
        except ValueError as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        hdrs = {k: v for k, v in self.headers.items()}

        try:
            if self.path == "/v1/sandbox/execute":
                payload = self._app.execute(raw)
            elif self.path == "/v1/sandbox/file/read":
                payload = self._app.file_read(raw, hdrs)
            elif self.path == "/v1/sandbox/file/write":
                payload = self._app.file_write(raw, hdrs)
            elif self.path == "/v1/sandbox/file/list":
                payload = self._app.file_list(raw, hdrs)
            elif self.path == "/v1/sandbox/file/grep":
                payload = self._app.file_grep(raw, hdrs)
            elif self.path == "/v1/sandbox/file/replace":
                payload = self._app.file_replace(raw, hdrs)
            elif self.path == "/v1/sandbox/shell/exec":
                payload = self._app.shell_exec(raw, hdrs)
            elif self.path == "/v1/sandbox/queue/drain":
                payload = self._app.queue_drain()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
        except Exception as e:
            _dbg("handler_error", error_type=type(e).__name__, error=str(e))
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                          {"error": f"{type(e).__name__}: {e}"})
            return
        self._send_json(HTTPStatus.OK, payload)

    def _handle(self, fn: Callable[[], Dict[str, Any]]) -> None:
        try:
            payload = fn()
        except KeyError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "result not found"})
            return
        except Exception as e:
            _dbg("handle_error", error_type=type(e).__name__, error=str(e))
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                          {"error": f"{type(e).__name__}: {e}"})
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
        return obj

    def _send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ---- Application ----

class SandboxHttpApp:
    def __init__(self, *, package_repo: ScriptPackageRepository,
                 container_queue: ContainerQueue | None = None,
                 file_manager: FileManager | None = None) -> None:
        self._package_repo = package_repo
        self._result_repo = InMemoryExecutionResultRepository()
        self._result = Result(result_repo=self._result_repo)

        parser_factory = DefaultScriptParserFactory()
        parser_factory.register(PythonScriptParser())
        parser_factory.register(BatScriptParser())

        self._request_parser = DefaultExecutionRequestParser(parser_factory=parser_factory)
        self._executor = DefaultScriptsExecutor(parser_factory=parser_factory)
        self._service = DefaultSandboxExecutionService(executor=self._executor, result_return=self._result)

        self._queue = container_queue
        self._file_manager = file_manager or FileManager()

    # ---- Legacy script-package execution ----

    def execute(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        dto = ExecuteRequestDTO.from_dict(raw)
        if not dto.package_id:
            return {"error": "missing package_id"}
        request_id = str(raw.get("request_id") or raw.get("id") or "").strip() or f"req_{uuid.uuid4().hex}"
        package = self._package_repo.get(dto.package_id)
        structured: Dict[str, Any] = {
            "request_id": request_id, "entry": dto.entry, "args": dto.args,
            "env": dto.env, "timeout_ms": dto.timeout_ms, "limits": dto.limits,
        }
        req = self._request_parser.parse(structured, package)
        result = asyncio.run(self._service.execute(req))
        return self._to_response_dto(result).to_dict()

    def get_result(self, request_id: str) -> Dict[str, Any]:
        result = self._result_repo.get(request_id)
        return self._to_response_dto(result).to_dict()

    def health_check(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"status": "ok"}
        if self._queue:
            payload["queue"] = self._queue.health_check()
        return payload

    # ---- File operations (queue lifecycle) ----

    def file_read(self, raw: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        uid, sid = _extract_tenant(headers)
        fp = str(raw.get("file") or "").strip()
        if not fp: return {"error": "missing 'file'"}
        max_chars = raw.get("max_chars")

        cid = None
        try:
            cid = self._acquire(uid, sid)
            content = self._exec_read(cid, fp)
            if max_chars and len(content) > max_chars:
                content = content[:max_chars]
            return {"content": content}
        finally:
            if cid:
                self._release(cid, uid, sid)

    def file_write(self, raw: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        uid, sid = _extract_tenant(headers)
        fp = str(raw.get("file") or "").strip()
        ct = str(raw.get("content") or "")
        if not fp: return {"error": "missing 'file'"}
        if not ct: return {"error": "missing 'content'"}

        cid = None
        try:
            cid = self._acquire(uid, sid)
            n_bytes = self._exec_write(cid, fp, ct)
            return {"file": fp, "bytes_written": n_bytes}
        finally:
            if cid:
                self._release(cid, uid, sid)

    def file_list(self, raw: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        uid, sid = _extract_tenant(headers)
        path = str(raw.get("path") or "/workspace").strip()
        recursive = bool(raw.get("recursive"))

        cid = None
        try:
            cid = self._acquire(uid, sid)
            cmd = f"find {_sh(path)} -maxdepth {1 if not recursive else 10} -mindepth 1 -printf '%y %s %f\\n' 2>/dev/null"
            stdout, _, _ = self._container_exec(cid, cmd)
            files = []
            for line in stdout.strip().split("\n"):
                if not line: continue
                parts = line.split(" ", 2)
                if len(parts) >= 3:
                    files.append({"name": parts[2], "size": int(parts[1]),
                                  "is_directory": parts[0] == "d"})
            return {"files": files, "total_count": len(files), "path": path}
        finally:
            if cid:
                self._release(cid, uid, sid)

    def file_grep(self, raw: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        uid, sid = _extract_tenant(headers)
        path = str(raw.get("path") or "").strip()
        pattern = str(raw.get("pattern") or "").strip()
        if not path: return {"error": "missing 'path'"}
        if not pattern: return {"error": "missing 'pattern'"}
        recursive = raw.get("recursive", True)
        ignore_case = raw.get("ignore_case", False)

        cid = None
        try:
            cid = self._acquire(uid, sid)
            flags = "-rHn" + ("i" if ignore_case else "")
            cmd = f"grep {flags} {_sh(pattern)} {_sh(path)} 2>/dev/null | head -50"
            stdout, _, _ = self._container_exec(cid, cmd)
            matches = []
            for line in stdout.strip().split("\n"):
                if not line: continue
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({"file": parts[0], "line_number": int(parts[1]),
                                    "line": parts[2]})
            return {"matches": matches, "count": len(matches)}
        finally:
            if cid:
                self._release(cid, uid, sid)

    def file_replace(self, raw: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        uid, sid = _extract_tenant(headers)
        fp = str(raw.get("file") or "").strip()
        old = raw.get("old_str", "")
        new = raw.get("new_str", "")
        if not fp: return {"error": "missing 'file'"}
        if not old: return {"error": "missing 'old_str'"}

        cid = None
        try:
            cid = self._acquire(uid, sid)
            # Read current content
            content = self._exec_read(cid, fp)
            if old not in content:
                return {"error": "old_str not found in file"}
            new_content = content.replace(old, new, 1)
            n_bytes = self._exec_write(cid, fp, new_content)
            return {"file": fp, "bytes_written": n_bytes}
        finally:
            if cid:
                self._release(cid, uid, sid)

    def shell_exec(self, raw: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        uid, sid = _extract_tenant(headers)
        cmd = str(raw.get("command") or "").strip()
        if not cmd: return {"error": "missing 'command'"}
        exec_dir = raw.get("exec_dir") or "/workspace"
        timeout_ms = raw.get("timeout_ms") or raw.get("timeout", 30000)

        cid = None
        try:
            cid = self._acquire(uid, sid)
            stdout, stderr, ec = self._container_exec(cid, cmd, exec_dir=exec_dir,
                                                       timeout=int(timeout_ms) // 1000)
            return {"exit_code": ec, "stdout": stdout, "stderr": stderr}
        finally:
            if cid:
                self._release(cid, uid, sid)

    def queue_drain(self) -> Dict[str, Any]:
        if not self._queue:
            return {"error": "queue not enabled"}
        # 快照所有 cid，释放锁后在锁外逐个 recycle（recycle 内部获取 _lock，防止死锁）
        cids = list(self._queue._containers.keys())
        recycled = 0
        for cid in cids:
            new_cid = self._queue.recycle(cid)
            if new_cid:
                recycled += 1
        return {"drained": recycled}

    # ---- Internal ----

    def _acquire(self, uid: str, sid: str) -> str:
        if not self._queue:
            raise RuntimeError("container queue not enabled (set SANDBOX_QUEUE_ENABLE=1)")
        cid = self._queue.acquire(uid, sid)
        self._file_manager.pull(cid, uid, sid)
        return cid

    def _release(self, cid: str, uid: str, sid: str) -> None:
        self._file_manager.push(cid, uid, sid)
        self._queue.release(cid)

    # ---- Docker exec helpers ----

    def _exec_read(self, cid: str, path: str) -> str:
        stdout, stderr, rc = self._container_exec(cid, f"cat {_sh(path)}")
        if rc != 0 and "No such file" in stderr:
            return ""
        return stdout

    def _exec_write(self, cid: str, path: str, content: str) -> int:
        data = content.encode("utf-8")
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            tmp.write(data)
            tmp.close()
            parent = path.rsplit("/", 1)[0] if "/" in path else "/workspace"
            self._container_exec(cid, f"mkdir -p {_sh(parent)}")
            result = subprocess.run(
                ["docker", "cp", tmp.name, f"{cid}:{path}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(f"docker cp failed: {result.stderr.strip()[:200]}")
            return len(data)
        finally:
            os.unlink(tmp.name)

    def _container_exec(self, cid: str, command: str, exec_dir: str = "",
                        timeout: int = 30) -> tuple[str, str, int]:
        args = ["docker", "exec"]
        if exec_dir:
            args.extend(["-w", exec_dir])
        args.extend([cid, "sh", "-c", command])
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "", "timeout", -1
        return completed.stdout or "", completed.stderr or "", completed.returncode

    def _to_response_dto(self, result: ExecutionResult) -> ExecuteResponseDTO:
        artifacts = []
        if result.artifacts:
            for a in result.artifacts:
                artifacts.append({"name": a.name, "uri": a.uri, "metadata": a.metadata})
        return ExecuteResponseDTO(
            request_id=result.request_id, status=result.status.value,
            sandbox_id=result.sandbox_id, exit_code=result.exit_code,
            stdout=result.stdout or "", stderr=result.stderr or "",
            duration_ms=result.duration_ms, artifacts=artifacts,
        )


# ---- Construction helpers ----

def _extract_tenant(headers: Dict[str, str]) -> tuple[str, str]:
    uid = (headers.get("X-User-Id") or headers.get("x-user-id") or "").strip()
    sid = (headers.get("X-Session-Id") or headers.get("x-session-id") or "").strip()
    if not uid or not sid:
        raise RuntimeError("missing X-User-Id or X-Session-Id header")
    return uid, sid


def _sh(s: str) -> str:
    """Minimal shell escaping for single-quoted strings."""
    escaped = s.replace("'", "'\\''")
    return f"'{escaped}'"


def build_sandbox_http_handler(*, app: SandboxHttpApp) -> type[BaseHTTPRequestHandler]:
    def _handler(*args: Any, **kwargs: Any) -> SandboxHttpHandler:
        return SandboxHttpHandler(*args, app=app, **kwargs)
    return _handler  # type: ignore[return-value]


def build_default_http_server(*, host: str, port: int, packages_base_dir: str,
                              container_queue: ContainerQueue | None = None,
                              file_manager: FileManager | None = None) -> StdHttpServer:
    repo = LocalFsScriptPackageRepository(packages_base_dir)
    app = SandboxHttpApp(package_repo=repo, container_queue=container_queue,
                         file_manager=file_manager)
    handler_cls = build_sandbox_http_handler(app=app)
    return StdHttpServer(host=host, port=port, handler_factory=handler_cls)


# ---- Entry point ----

if __name__ == "__main__":
    import signal, sys

    host = os.getenv("SANDBOX_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = os.getenv("SANDBOX_PORT", "9001").strip() or "9001"
    packages_dir = os.getenv("SANDBOX_PACKAGES_DIR", "./packages").strip() or "./packages"

    use_queue = os.getenv("SANDBOX_QUEUE_ENABLE", "").strip().lower() in ("1", "true", "yes")
    queue = None
    file_mgr = None
    watcher = None

    if use_queue:
        workspace_cache = os.getenv("AIO_WORKSPACE_CACHE_DIR", "/workspaces")
        queue = ContainerQueue(
            image=os.getenv("AIO_WORKER_IMAGE", "ghcr.io/agent-infra/sandbox:latest"),
            min_idle=int(os.getenv("AIO_WORKER_MIN_IDLE", "2")),
            max_total=int(os.getenv("AIO_WORKER_MAX_TOTAL", "8")),
            workspace_cache=workspace_cache,
        )
        file_mgr = FileManager(workspace_cache=workspace_cache)
        print(f"[sandbox] pre-fetching {queue._min_idle} containers...")
        queue.ensure_idle_count()

        watcher = Watcher(
            queue,
            dirty_ttl=float(os.getenv("AIO_WORKER_DIRTY_TTL", "60")),
        )
        watcher.start()
        print("[sandbox] container queue watcher started.")

    def _shutdown(signum=None, frame=None):
        print("[sandbox] shutting down...")
        if watcher:
            watcher.stop()
        if queue:
            print("[sandbox] removing containers...")
            for cid in list(queue._containers.keys()):
                try:
                    queue._rm_container(cid)
                except Exception:
                    pass
            print("[sandbox] containers removed.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    server = build_default_http_server(
        host=host, port=int(port_raw), packages_base_dir=packages_dir,
        container_queue=queue, file_manager=file_mgr,
    )
    server.start()
