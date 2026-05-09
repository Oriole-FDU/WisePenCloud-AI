import asyncio
import shutil
from pathlib import Path
from typing import Optional

from common.logger import log_ok, log_fail, log_error

__all__ = [
    "LocalScriptFetcher",
]

MAX_SUBPROCESS_BUFFER = 10 * 1024 * 1024
MAX_ERROR_SNIPPET = 500

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "local_web_fetcher.js"


async def kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.kill()

    try:
        await asyncio.wait_for(process.communicate(), timeout=5)
    except Exception:
        await process.wait()


class LocalScriptFetcher:
    def __init__(self, timeout: float = 120.0):
        if not SCRIPT_PATH.is_file():
            log_error("本地脚本初始化", f"未找到 JS 脚本: {SCRIPT_PATH}")
            raise FileNotFoundError(f"未找到 JS 脚本: {SCRIPT_PATH}")

        node_path = shutil.which("node") or shutil.which("node.exe")

        if not node_path or not Path(node_path).is_file():
            log_error("本地脚本初始化", "未检测到 Node.js 运行环境，请确认 Node.js 已安装并加入 PATH")
            raise FileNotFoundError("未检测到 Node.js 运行环境，请确认 Node.js 已安装并加入 PATH")

        self._node_path = node_path
        self._timeout = timeout

        log_ok("本地脚本初始化", node_path=self._node_path, timeout=self._timeout)

    async def fetch(self, url: str) -> Optional[str]:
        process = None

        try:
            process = await asyncio.create_subprocess_exec(
                self._node_path,
                str(SCRIPT_PATH),
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_SUBPROCESS_BUFFER,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout,
            )

            if process.returncode != 0:
                err_msg = (
                    stderr.decode("utf-8", errors="replace").strip()[:MAX_ERROR_SNIPPET]
                    if stderr
                    else ""
                )
                log_fail("本地脚本执行", f"退出码 {process.returncode}: {err_msg}", url=url)
                return None

            markdown = stdout.decode("utf-8", errors="replace").strip()

            if not markdown:
                log_fail("本地脚本执行", "抓取内容为空", url=url)
                return None

            return markdown

        except asyncio.TimeoutError:
            log_fail("本地脚本执行", f"超时 {self._timeout}s", url=url)

            if process:
                await kill_process(process)

            return None

        except Exception as e:
            log_error("本地脚本执行", e, url=url)

            if process:
                await kill_process(process)

            return None