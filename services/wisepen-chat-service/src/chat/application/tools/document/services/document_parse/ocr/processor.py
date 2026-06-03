import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Mapping

from chat.application.tools.document.services.document_parse.ocr.errors import (
    OcrProcessingError,
    OcrWorkerError,
)
from common.logger import log_event, log_fail

SHUTDOWN_REQUEST: Dict[str, object] = {"shutdown": True}


@dataclass(slots=True)
class OcrResult:
    """表示当前组件。"""
    ok: bool
    text: str = ""
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class OcrWorkerRequest:
    input_path: str

    def to_payload(self) -> Dict[str, object]:
        return {"input": self.input_path}


@dataclass(frozen=True, slots=True)
class OcrWorkerResponse:
    ok: bool
    text: Optional[str]
    error: Optional[str]
    message: Optional[str]


@dataclass(frozen=True, slots=True)
class OcrProcessorConfig:
    worker_env: Mapping[str, str] = field(
        default_factory=lambda: {
            "FLAGS_use_mkldnn": "0",
            "FLAGS_enable_onednn": "0",
            "FLAGS_cpu_math_library_num_threads": "1",
            "DNNL_MAX_CPU_ISA": "NONE",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    timeout_seconds: float = 120.0
    worker_idle_ttl_seconds: int = 30 * 60
    worker_request_max_attempts: int = 2
    worker_response_max_non_json_lines: int = 20
    worker_shutdown_timeout_seconds: float = 5.0
    worker_stderr_error_keywords: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "error",
                "traceback",
                "exception",
                "failed",
            }
        )
    )


class OcrProcessor:
    """本地 PaddleOCR worker 处理器。"""

    def __init__(self, *, config: OcrProcessorConfig):
        self._config = config
        self._worker_process: Optional[asyncio.subprocess.Process] = None
        self._worker_lock = asyncio.Lock()
        self._worker_stderr_task: Optional[asyncio.Task] = None
        self._worker_stderr_process: Optional[asyncio.subprocess.Process] = None
        self._idle_reaper_task: Optional[asyncio.Task] = None
        self._last_worker_use = 0.0

    async def recognize_image(self, image_path: Path) -> OcrResult:
        try:
            # 定义内部执行体以配合 asyncio.wait_for 的超时控制
            async def _run_ocr() -> OcrResult:
                """处理当前流程。"""
                if not image_path.is_file():
                    raise OcrProcessingError(f"OCR input file not found: {image_path}")


                response = await self._request_worker(
                    OcrWorkerRequest(input_path=str(image_path))
                )

                if response.ok:
                    text = (response.text or "").strip()
                else:
                    message = response.message or response.error or "Unknown OCR worker error"
                    raise OcrProcessingError(message)

                if not text:
                    return OcrResult(ok=False, error="OCR produced no text.")

                return OcrResult(ok=True, text=text)

            return await asyncio.wait_for(
                _run_ocr(),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            async with self._worker_lock:
                await self._stop_worker(force=True)
            log_fail(
                "OCR 识别",
                "请求超时",
                path=str(image_path),
                timeout=self._config.timeout_seconds,
            )
            return OcrResult(ok=False, error="OCR processing timed out.")
        except OcrProcessingError as e:
            log_fail("OCR 识别", e, path=str(image_path))
            return OcrResult(ok=False, error=str(e))
        except Exception as e:
            log_fail("OCR 识别", e, path=str(image_path))
            return OcrResult(ok=False, error=f"{e.__class__.__name__}: {e}")


    async def _request_worker(
            self,
            request: OcrWorkerRequest,
    ) -> OcrWorkerResponse:
        """处理当前流程。"""
        for attempt in range(self._config.worker_request_max_attempts):
            async with self._worker_lock:
                process = await self._ensure_worker()
                try:

                    if process.stdin is None or process.stdout is None:
                        raise OcrWorkerError("OCR worker 标准输入或标准输出不可用")

                    process.stdin.write(
                        json.dumps(request.to_payload(), ensure_ascii=False).encode("utf-8")
                        + b"\n"
                    )
                    await process.stdin.drain()


                    # 执行事务：读取 Worker 响应
                    response = await self._read_worker_response(process)

                    self._last_worker_use = time.monotonic()
                    self._ensure_idle_reaper()

                    return response

                except Exception as e:
                    await self._stop_worker(force=True)

                    if attempt == 0:
                        log_fail("OCR worker 请求", repr(e))
                        continue

                    raise

        raise OcrWorkerError("OCR worker request failed")

    async def _read_worker_response(
            self,
            process: asyncio.subprocess.Process,
    ) -> OcrWorkerResponse:
        """读取当前流程。"""
        for _ in range(self._config.worker_response_max_non_json_lines):

            if process.stdout is None:
                raise OcrWorkerError("OCR worker 标准输出不可用")

            line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=self._config.timeout_seconds,
            )
            if not line:
                raise OcrWorkerError("OCR worker 在返回响应前退出")

            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            try:
                parsed: object = json.loads(decoded)
            except json.JSONDecodeError:
                log_fail("OCR worker 协议", f"stdout={decoded}")
                continue

            return OcrWorkerResponseParser.parse(parsed)

        raise OcrWorkerError("OCR worker 返回了过多非 JSON 行")


    async def _ensure_worker(self) -> asyncio.subprocess.Process:
        # 阶段一：验证现有进程活性
        """处理当前流程。"""
        if self._worker_process is not None and self._worker_process.returncode is None:
            return self._worker_process

        if self._worker_process is not None:
            await self._stop_worker(force=True)

        # 阶段二：全新拉起并重置指标
        process = await self._start_worker()

        self._worker_process = process
        self._last_worker_use = time.monotonic()
        self._ensure_idle_reaper()

        return process

    async def _start_worker(self) -> asyncio.subprocess.Process:

        # 计算环境路径依赖
        current = Path(__file__).resolve()
        src_root = current.parents[3]
        service_root = current.parents[4]


        # 组装子进程环境变量
        env = os.environ.copy()
        env.update(self._config.worker_env)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(src_root)
            if not existing_pythonpath
            else str(src_root) + os.pathsep + existing_pythonpath
        )


        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "chat.application.tools.common.ocr.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(service_root),
            env=env,
        )

        self._worker_stderr_task = asyncio.create_task(
            self._drain_worker_stderr(process)
        )
        self._worker_stderr_process = process
        log_event("OCR worker 启动", pid=process.pid)
        return process

    async def _drain_worker_stderr(
            self,
            process: asyncio.subprocess.Process,
    ) -> None:

        if process.stderr is None:
            return

        # 持续轮询管道流
        while True:
            line = await process.stderr.readline()
            if not line:
                return

            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            # 关键字监控分类分流
            lower = decoded.lower()
            if any(
                    keyword in lower
                    for keyword in self._config.worker_stderr_error_keywords
            ):
                log_event("OCR worker stderr", stderr=decoded, level="error")
            else:
                log_event("OCR worker stderr", stderr=decoded)


    def _ensure_idle_reaper(self) -> None:

        if self._idle_reaper_task is not None and not self._idle_reaper_task.done():
            return

        self._idle_reaper_task = asyncio.create_task(self._idle_reaper())


    async def _idle_reaper(self) -> None:

        try:
            # 周期性轮询监控
            while True:
                sleep_seconds = min(
                    max(self._config.worker_idle_ttl_seconds / 4, 1.0),
                    60.0,
                )
                await asyncio.sleep(sleep_seconds)

                # 独占锁判定生存状态与时钟差度
                async with self._worker_lock:
                    process = self._worker_process
                    if process is None or process.returncode is not None:
                        break

                    idle_seconds = time.monotonic() - self._last_worker_use
                    if idle_seconds >= self._config.worker_idle_ttl_seconds:
                        await self._stop_worker()
                        log_event(
                            "OCR worker 空闲退出",
                            idle_seconds=round(idle_seconds, 2),
                        )
                        break
        finally:
            self._idle_reaper_task = None


    async def close(self) -> None:
        """关闭当前流程。"""
        idle_task = self._idle_reaper_task
        self._idle_reaper_task = None

        if idle_task is not None and not idle_task.done():
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

        async with self._worker_lock:
            await self._stop_worker()


    async def _stop_worker(self, *, force: bool = False) -> None:
        """停止当前流程。"""
        process = self._worker_process
        self._worker_process = None

        if process is None:
            return

        try:
            if process.returncode is None and process.stdin is not None and not force:
                process.stdin.write(json.dumps(SHUTDOWN_REQUEST).encode("utf-8") + b"\n")
                await process.stdin.drain()
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._config.worker_shutdown_timeout_seconds,
                )
            elif process.returncode is None:
                process.terminate()
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._config.worker_shutdown_timeout_seconds,
                )
        except Exception:
            # 温和关闭超时或崩溃，无条件进行系统级硬核熔断 (Kill)
            if process.returncode is None:
                process.kill()
                await process.wait()


        task = self._worker_stderr_task
        if task is not None and self._worker_stderr_process is process:
            self._worker_stderr_task = None
            self._worker_stderr_process = None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class OcrWorkerResponseParser:
    """解析并校验 OCR worker 返回的 JSON 响应为 OcrWorkerResponse。"""

    @staticmethod
    def parse(value: object) -> OcrWorkerResponse:
        if not isinstance(value, dict):
            raise OcrWorkerError("OCR worker 返回的 JSON 不是对象")

        payload: Dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise OcrWorkerError("OCR worker 返回的 JSON 对象存在非字符串 key")
            payload[key] = item

        return OcrWorkerResponse(
            ok=OcrWorkerResponseParser._required_bool(payload, "ok"),
            text=OcrWorkerResponseParser._optional_string(payload, "text"),
            error=OcrWorkerResponseParser._optional_string(payload, "error"),
            message=OcrWorkerResponseParser._optional_string(payload, "message"),
        )

    @staticmethod
    def _required_bool(payload: Dict[str, object], field_name: str) -> bool:
        value = payload.get(field_name)
        if not isinstance(value, bool):
            raise OcrWorkerError(
                f"OCR worker response field {field_name} must be bool."
            )
        return value

    @staticmethod
    def _optional_string(payload: Dict[str, object], field_name: str) -> Optional[str]:
        value = payload.get(field_name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise OcrWorkerError(
                f"OCR worker response field {field_name} must be string."
            )
        return value
