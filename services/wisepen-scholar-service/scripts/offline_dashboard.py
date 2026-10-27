"""本地离线任务看板：读取进度快照；run 包装器记录真实退出码，不访问业务数据库。"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = SERVICE_ROOT / ".runtime" / "offline-dashboard"
PAGE = Path(__file__).with_name("offline_dashboard.html")


def now() -> str:
    return datetime.now(UTC).isoformat()


def save_state(path: Path, state: dict) -> None:
    # 浏览器只读完整快照；ETL 写库期间也无需争用 DuckDB 文件锁。
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # Windows 的 os.kill 不是 POSIX 的探活语义，使用只读进程句柄。
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # 进程已不存在。
                return False
            raise ctypes.WinError(error)
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def run_job(args: argparse.Namespace) -> int:
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("run requires a command after --")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.job):
        raise ValueError("job must contain only letters, numbers, _ or -")
    args.root.mkdir(parents=True, exist_ok=True)
    state_path = args.root / f"{args.job}.json"
    if state_path.exists():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if previous["status"] == "running" and process_alive(previous["pid"]):
            raise RuntimeError(f"job is already running: {args.job}")
    state = {
        "id": args.job,
        "name": args.name or args.job,
        "status": "running",
        "pid": os.getpid(),
        "started_at": now(),
        "updated_at": now(),
        "stages": {},
        "exit_code": None,
    }
    # 日志不截断；同名重跑保留前次失败证据。命令参数不写入看板，避免记录凭证。
    log_path = args.root / f"{args.job}.log"
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        log.write(f"\n--- {state['started_at']} 开始运行 ---\n")
        save_state(state_path, state)
        try:
            with subprocess.Popen(
                command,
                cwd=args.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={
                    **os.environ,
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONIOENCODING": "utf-8",
                },
            ) as child:
                state["child_pid"] = child.pid
                save_state(state_path, state)
                samples: dict[str, tuple[float, int]] = {}
                for line in child.stdout:
                    log.write(line)
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        # stdout 同时包含 JSON 进度及普通日志；原文始终保留供诊断。
                        continue
                    if not isinstance(event, dict) or "stage" not in event:
                        continue
                    stage = event["stage"]
                    state["stage"] = stage
                    state["updated_at"] = now()
                    entry = {"event": event, "updated_at": state["updated_at"]}
                    # 只在同一计数范围内计算速率，跨分片或阶段不拼接错误的分母。
                    scope = f"{stage}:{event.get('source_path', '')}"
                    counter = next(
                        (
                            event[k]
                            for k in (
                                "written",
                                "processed",
                                "staged_changed",
                                "scanned",
                                "pending",
                                "pending_publish",
                            )
                            if isinstance(event.get(k), int)
                        ),
                        None,
                    )
                    tick = time.monotonic()
                    if counter is not None:
                        if scope in samples:
                            first_tick, first_count = samples[scope]
                            entry["rate"] = abs(counter - first_count) / max(
                                tick - first_tick, 0.001
                            )
                        else:
                            samples[scope] = tick, counter
                    state["stages"][stage] = entry
                    save_state(state_path, state)
                code = child.wait()
        except BaseException as error:
            # 启动失败、取消及监控异常都保留原始 traceback，并继续向调用方抛出。
            traceback.print_exc(file=log)
            state.update(
                status="failed" if isinstance(error, Exception) else "interrupted",
                finished_at=now(),
            )
            save_state(state_path, state)
            raise
        state.update(
            status="succeeded" if code == 0 else "failed",
            exit_code=code,
            finished_at=now(),
        )
        save_state(state_path, state)
    return code


def tail(path: Path, limit: int = 18_000) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read().decode("utf-8", errors="replace")


def serve(args: argparse.Namespace) -> None:
    args.root.mkdir(parents=True, exist_ok=True)
    watched = {}
    for item in args.log:
        name, separator, filename = item.partition("=")
        if not separator:
            raise ValueError("--log must use name=path")
        path = Path(filename).resolve(strict=True)
        watched[name] = path

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = urlsplit(self.path).path
            if route == "/":
                body, content_type = PAGE.read_bytes(), "text/html; charset=utf-8"
            elif route == "/api/status":
                jobs = []
                for path in args.root.glob("*.json"):
                    if path.name == "baseline.json":
                        continue
                    state = json.loads(path.read_text(encoding="utf-8"))
                    if state["status"] == "running" and not process_alive(state["pid"]):
                        # 包装器异常消失时不能宣称成功，也不能凭日志更新时间判为失败。
                        state["status"] = "interrupted"
                    state["log"] = tail(path.with_suffix(".log"))
                    jobs.append(state)
                baseline_path = args.root / "baseline.json"
                payload = {
                    "time": now(),
                    "jobs": sorted(
                        jobs, key=lambda job: job["started_at"], reverse=True
                    ),
                    "baseline": json.loads(baseline_path.read_text(encoding="utf-8"))
                    if baseline_path.exists()
                    else None,
                    "history": [
                        {"name": name, "log": tail(path)}
                        for name, path in watched.items()
                    ],
                }
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *values) -> None:
            # 三秒刷新无需刷屏；任务日志由 run 独立保留。
            if format == '"%s" %s %s' and int(values[1]) < 400:
                return
            super().log_message(format, *values)

    # 只监听本机；不加载 Nacos、数据库或业务服务，也不提供启动/停止任务的网页接口。
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Offline dashboard: http://127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="action", required=True)
    dashboard = commands.add_parser("serve")
    dashboard.add_argument("--port", type=int, default=19917)
    dashboard.add_argument(
        "--log", action="append", default=[], help="额外展示历史日志，格式 name=path"
    )
    job = commands.add_parser("run")
    job.add_argument("--name")
    job.add_argument("--cwd", type=Path, default=SERVICE_ROOT)
    job.add_argument("job")
    job.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    return run_job(args) if args.action == "run" else serve(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
