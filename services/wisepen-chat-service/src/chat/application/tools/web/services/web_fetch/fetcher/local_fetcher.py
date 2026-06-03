import asyncio
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from chat.application.tools.web.services.web_fetch.enums import FetcherName
from chat.application.tools.web.services.web_fetch.fetcher.base import BaseFetcher
from chat.application.tools.web.services.web_fetch.models import FetchedLink, FetchedPage
from chat.application.tools.web.utils.domains import extract_domain
from chat.application.tools.web.utils.markdown import extract_markdown_title
from common.logger import log_error, log_event, log_fail

_MAX_SUBPROCESS_BUFFER = 10 * 1024 * 1024
_MAX_ERROR_SNIPPET = 500
_PROCESS_KILL_TIMEOUT_SECONDS = 5.0

_WORKER_SCRIPT_PATH = Path(__file__).resolve().parent / "local_js" / "local_web_fetcher_worker.js"


def _resolve_node_path() -> str:
    """解析系统中 Node.js 可执行文件的路径，未找到时抛出异常。"""
    node_path = shutil.which("node") or shutil.which("node.exe")

    if node_path:
        return node_path

    message = "未检测到 Node.js 运行环境，请确认 Node.js 已安装并加入 PATH"
    log_error("本地脚本初始化", message)
    raise FileNotFoundError(message)


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    """强制终止子进程，超时未退出时记录失败日志。"""
    if process.returncode is not None:
        return

    process.kill()

    try:
        await asyncio.wait_for(process.wait(), timeout=_PROCESS_KILL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log_fail("本地脚本执行", "子进程 kill 后仍未退出")


@dataclass(slots=True)
class LocalWorker:
    """管理单个 Node.js 子进程工作器的生命周期，负责通信、抓取及自动重启。"""

    index: int
    node_path: str
    script_path: Path
    timeout: float
    restart_after: int
    in_process_concurrency: int
    process: Optional[asyncio.subprocess.Process] = None
    stdout_task: Optional[asyncio.Task[None]] = None
    stderr_task: Optional[asyncio.Task[None]] = None
    handled_count: int = 0
    lifecycle_lock: asyncio.Lock = field(init=False)
    write_lock: asyncio.Lock = field(init=False)
    capacity: asyncio.Semaphore = field(init=False)
    pending: Dict[str, asyncio.Future[Dict[str, Any]]] = field(init=False)

    def __post_init__(self) -> None:
        """初始化锁、信号量和待处理请求字典等运行时状态。"""
        self.lifecycle_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.capacity = asyncio.Semaphore(max(1, self.in_process_concurrency))
        self.pending = {}

    async def start(self) -> None:
        """在生命周期锁保护下启动子进程。"""
        async with self.lifecycle_lock:
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
        """创建并启动 Node.js 子进程，同时启动 stdout/stderr 消费任务。"""
        if self.process is not None and self.process.returncode is None:
            return

        env = {
            **os.environ,
            "WEB_FETCH_JS_WORKER_CONCURRENCY": str(self.in_process_concurrency),
            "WEB_FETCH_JS_BROWSER_RESTART_AFTER": str(self.restart_after),
        }

        self.process = await asyncio.create_subprocess_exec(
            self.node_path,
            str(self.script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_MAX_SUBPROCESS_BUFFER,
            env=env,
        )
        self.stdout_task = asyncio.create_task(self._consume_stdout())
        self.stderr_task = asyncio.create_task(self._consume_stderr())

        log_event(
            "LocalScriptFetcher worker 已启动",
            worker=self.index,
            pid=self.process.pid,
        )

    async def fetch(self, url: str) -> Optional[FetchedPage]:
        """向工作器发送 URL 抓取请求，等待 JSON 响应并解析为结构化结果。"""
        async with self.capacity:
            await self.start()

            process = self.process
            if process is None or process.stdin is None:
                await self.restart()
                return None

            if process.returncode is not None:
                log_fail(
                    "本地脚本执行",
                    "worker already exited",
                    url=url,
                    worker=self.index,
                )
                await self._restart_if_same_process(process)
                return None

            # 生成唯一请求 ID，通过 Future 等待异步响应
            request_id = uuid.uuid4().hex
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Dict[str, Any]] = loop.create_future()
            self.pending[request_id] = future

            # 通过 stdin 向子进程发送 JSON 格式的抓取请求
            payload = json.dumps(
                {"id": request_id, "url": url},
                ensure_ascii=False,
            ) + "\n"

            try:
                async with self.write_lock:
                    process.stdin.write(payload.encode("utf-8"))
                    await process.stdin.drain()

                response = await asyncio.wait_for(future, timeout=self.timeout)
            except asyncio.TimeoutError:
                log_fail(
                    "本地脚本执行",
                    f"worker timeout: {self.timeout}s",
                    url=url,
                    worker=self.index,
                )
                await self._restart_if_same_process(process)
                return None
            except Exception as e:
                log_error("本地脚本执行", e, url=url, worker=self.index)
                await self._restart_if_same_process(process)
                return None

            # 校验响应 ID 是否与请求 ID 匹配
            if response.get("id") != request_id:
                log_fail(
                    "本地脚本执行",
                    "worker response id mismatch",
                    url=url,
                    worker=self.index,
                )
                await self._restart_if_same_process(process)
                return None

            if not response.get("ok"):
                log_fail(
                    "本地脚本执行",
                    str(response.get("error") or "unknown worker error")[
                        :_MAX_ERROR_SNIPPET
                    ],
                    url=url,
                    worker=self.index,
                )
                return None

            markdown = str(response.get("markdown") or "").strip()
            if not markdown:
                log_fail("本地脚本执行", "empty result", url=url, worker=self.index)
                return None

            # 解析链接列表，兼容驼峰和下划线两种字段命名
            links = [
                FetchedLink(
                    url=str(link.get("url") or ""),
                    anchor_text=str(
                        link.get("anchorText") or link.get("anchor_text") or ""
                    ),
                    surrounding_text=str(
                        link.get("surroundingText")
                        or link.get("surrounding_text")
                        or ""
                    ),
                )
                for link in response.get("links") or []
                if isinstance(link, dict) and str(link.get("url") or "").strip()
            ]

            self.handled_count += 1

            raw_final_url = response.get("finalUrl")
            final_url = raw_final_url.strip() if isinstance(raw_final_url, str) and raw_final_url.strip() else None
            raw_title = response.get("title")
            title = (
                raw_title.strip()
                if isinstance(raw_title, str) and raw_title.strip()
                else extract_markdown_title(markdown)
            )

            return FetchedPage(
                markdown=markdown,
                links=links,
                title=title,
                final_url=final_url,
                domain=extract_domain(final_url) if final_url else None,
                status_code=response.get("statusCode"),
            )

    async def restart(self) -> None:
        """关闭当前子进程并重新启动，重置处理计数。"""
        async with self.lifecycle_lock:
            await self._close_unlocked()
            self.handled_count = 0

            try:
                await self._start_unlocked()
            except Exception as e:
                log_error("LocalScriptFetcher worker 重启", e, worker=self.index)

    async def _restart_if_same_process(self, process: asyncio.subprocess.Process) -> None:
        """确认子进程未发生变化后执行重启。"""
        async with self.lifecycle_lock:
            if self.process is not process:
                return

            await self._close_unlocked()
            self.handled_count = 0

            try:
                await self._start_unlocked()
            except Exception as e:
                log_error("LocalScriptFetcher worker 重启", e, worker=self.index)

    async def close(self) -> None:
        """关闭子进程并清理所有待处理请求。"""
        async with self.lifecycle_lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        """关闭子进程、取消消费任务、将待处理请求置为异常。"""
        process = self.process
        self.process = None

        # 将所有待处理 Future 标记为异常，避免请求永远挂起
        pending = self.pending
        self.pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(RuntimeError("worker restarted"))

        # 取消 stdout 消费任务
        stdout_task = self.stdout_task
        self.stdout_task = None
        if stdout_task is not None:
            stdout_task.cancel()
            try:
                await stdout_task
            except asyncio.CancelledError:
                pass

        # 取消 stderr 消费任务
        stderr_task = self.stderr_task
        self.stderr_task = None
        if stderr_task is not None:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

        if process is None:
            return

        if process.returncode is not None:
            return

        # 先优雅终止，超时则强制 kill
        process.terminate()

        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=_PROCESS_KILL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _kill_process(process)

    async def _consume_stdout(self) -> None:
        """持续读取子进程 stdout，解析 JSON 响应并设置对应 Future 的结果。"""
        process = self.process
        if process is None or process.stdout is None:
            return

        try:
            while True:
                line = await process.stdout.readline()

                if not line:
                    # stdout 关闭，将所有待处理请求置为异常
                    pending = self.pending
                    self.pending = {}
                    for future in pending.values():
                        if not future.done():
                            future.set_exception(RuntimeError("worker stdout closed"))
                    return

                try:
                    response = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError as e:
                    log_fail(
                        "本地脚本执行",
                        f"invalid worker json: {e}",
                        worker=self.index,
                    )
                    continue

                # 根据请求 ID 查找对应的 Future 并设置结果
                request_id = str(response.get("id") or "")
                future = self.pending.pop(request_id, None)

                if future is None:
                    log_fail(
                        "本地脚本执行",
                        "worker response id not pending",
                        worker=self.index,
                        request_id=request_id,
                    )
                    continue

                if not future.done():
                    future.set_result(response)

        except asyncio.CancelledError:
            return
        except Exception as e:
            log_error("LocalScriptFetcher worker stdout", e, worker=self.index)

    async def _consume_stderr(self) -> None:
        """持续读取子进程 stderr 并记录日志。"""
        process = self.process
        if process is None or process.stderr is None:
            return

        try:
            while True:
                line = await process.stderr.readline()

                if not line:
                    return

                message = line.decode("utf-8", errors="replace").strip()
                if message:
                    log_event(
                        "LocalScriptFetcher worker stderr",
                        message=message[:_MAX_ERROR_SNIPPET],
                        worker=self.index,
                    )

        except asyncio.CancelledError:
            return
        except Exception as e:
            log_error("LocalScriptFetcher worker stderr", e, worker=self.index)


class LocalScriptFetcher(BaseFetcher):
    """本地 JS 脚本抓取兜底器，管理和调度多个 LocalWorker 工作器。"""

    @property
    def name(self) -> FetcherName:
        """返回当前抓取器的唯一标识名称。"""
        return FetcherName.LOCAL_JS

    def __init__(
        self,
        timeout: float = 30.0,
        worker_count: int = 5,
        restart_after: int = 200,
        worker_concurrency: int = 2,
    ):
        """初始化工作器池，探测 Node.js 路径并创建工作器队列。"""
        self._node_path = _resolve_node_path()
        self._timeout = timeout
        self._worker_count = worker_count
        self._restart_after = restart_after
        self._worker_concurrency = worker_concurrency
        self._workers = [
            LocalWorker(
                index=index,
                node_path=self._node_path,
                script_path=_WORKER_SCRIPT_PATH,
                timeout=self._timeout,
                restart_after=self._restart_after,
                in_process_concurrency=self._worker_concurrency,
            )
            for index in range(worker_count)
        ]
        self._idle_workers: asyncio.Queue[LocalWorker] = asyncio.Queue()
        self._started = False
        self._start_lock = asyncio.Lock()

        log_event(
            "LocalScriptFetcher 初始化",
            node_path=self._node_path,
            timeout=self._timeout,
            worker_count=self._worker_count,
            worker_concurrency=self._worker_concurrency,
            restart_after=self._restart_after,
        )

    async def fetch(self, url: str) -> Optional[FetchedPage]:
        """从工作池中获取空闲工作器执行抓取。"""
        try:
            await self._ensure_pool_started()
        except Exception as e:
            log_error("LocalScriptFetcher worker pool 启动", e, url=url)
            return None

        try:
            worker = await asyncio.wait_for(
                self._idle_workers.get(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            log_fail(
                "LocalScriptFetcher worker pool",
                f"idle worker checkout timeout: {self._timeout}s",
                url=url,
                started=self._started,
                idle_qsize=self._idle_workers.qsize(),
                worker_count=self._worker_count,
                worker_concurrency=self._worker_concurrency,
            )

            self._started = False
            self._drain_idle_workers()
            return None

        try:
            return await worker.fetch(url)
        except Exception as e:
            log_error(
                "LocalScriptFetcher worker pool fetch",
                e,
                url=url,
                worker=worker.index,
            )
            return None
        finally:
            if self._started:
                await self._idle_workers.put(worker)

    async def close(self) -> None:
        """关闭所有工作器并清空闲队列。"""
        for worker in self._workers:
            await worker.close()

        self._started = False
        self._drain_idle_workers()

        log_event("LocalScriptFetcher worker pool 已关闭")

    async def _ensure_pool_started(self) -> None:
        """启动所有工作器并填充空闲队列。"""
        if self._started:
            return

        async with self._start_lock:
            if self._started:
                return

            started_workers: List[LocalWorker] = []

            try:
                for worker in self._workers:
                    await worker.start()
                    started_workers.append(worker)
            except Exception:
                # 部分启动失败时回滚已启动的 worker
                for worker in started_workers:
                    await worker.close()

                self._drain_idle_workers()
                self._started = False
                raise

            self._drain_idle_workers()

            # 每个 worker 按并发度多次入队以支持多路复用
            for worker in started_workers:
                for _ in range(max(1, self._worker_concurrency)):
                    await self._idle_workers.put(worker)

            self._started = True

            log_event(
                "LocalScriptFetcher worker pool 已启动",
                worker_count=self._worker_count,
            )

    def _drain_idle_workers(self) -> None:
        """清空空闲工作器队列。"""
        while not self._idle_workers.empty():
            try:
                self._idle_workers.get_nowait()
            except asyncio.QueueEmpty:
                break