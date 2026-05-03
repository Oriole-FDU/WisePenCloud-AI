import asyncio
import shutil
from pathlib import Path
from typing import Optional

from common.logger import log_ok, log_fail, log_error

_MAX_DIR_TRAVERSAL = 10  # 向上查找项目根目录的最大层级
_MAX_SUBPROCESS_BUFFER = 10 * 1024 * 1024  # 子进程 stdout 缓冲区上限（字节）
_MAX_ERROR_SNIPPET = 500  # 截取 stderr 错误信息的最大字符数


def _find_root_dir() -> Path:
    """向上查找包含 scripts/local_web_fetcher.js 的最近父目录"""
    current = Path(__file__).resolve().parent
    for _ in range(_MAX_DIR_TRAVERSAL):
        if (current / "scripts" / "local_web_fetcher.js").exists():
            return current
        current = current.parent
    raise FileNotFoundError("找不到项目根目录（包含 scripts/local_web_fetcher.js）")


ROOT_DIR = _find_root_dir()  # 项目根目录，用于定位脚本等资源
SCRIPT_PATH = ROOT_DIR / "scripts" / "local_web_fetcher.js"  # 本地浏览器抓取脚本路径


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
                limit=_MAX_SUBPROCESS_BUFFER,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout,
            )

            if process.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()[:_MAX_ERROR_SNIPPET] if stderr else ""
                log_fail("本地脚本执行", f"退出码 {process.returncode}: {err_msg}", url=url)
                return None

            markdown = stdout.decode("utf-8").strip()
            if not markdown:
                log_fail("本地脚本执行", "抓取内容为空", url=url)
                return None

            return markdown

        except asyncio.TimeoutError:
            log_fail("本地脚本执行", f"超时 {self._timeout}s", url=url)
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            return None

        except Exception as e:
            log_error("本地脚本执行", e, url=url)
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            return None
