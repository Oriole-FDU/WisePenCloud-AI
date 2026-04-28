import asyncio
import shutil
from pathlib import Path
from typing import Optional

from common.logger import log_ok, log_fail, log_error


def _find_root_dir() -> Path:
    """向上查找包含 scripts/local_web_fetcher.js 的最近父目录"""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "scripts" / "local_web_fetcher.js").exists():
            return current
        current = current.parent
    raise FileNotFoundError("找不到项目根目录（包含 scripts/local_web_fetcher.js）")


ROOT_DIR = _find_root_dir()
SCRIPT_PATH = ROOT_DIR / "scripts" / "local_web_fetcher.js"


class LocalScriptFetcher:
    def __init__(self, timeout: float = 120.0, min_content_length: int = 50):
        if not SCRIPT_PATH.is_file():
            log_error("本地脚本初始化", f"未找到 JS 脚本: {SCRIPT_PATH}")
            raise FileNotFoundError(f"未找到 JS 脚本: {SCRIPT_PATH}")

        node_path = (
            shutil.which("node")
            or shutil.which("node.exe")
            or r"c:\Users\12732\.trae-cn\sdks\versions\node\current\node.exe"
        )
        if not Path(node_path).is_file():
            log_error("本地脚本初始化", f"未找到 Node.js: {node_path}")
            raise FileNotFoundError(f"未找到 Node.js: {node_path}")

        self._node_path = node_path
        self._timeout = timeout
        self._min_content_length = min_content_length

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
                limit=10 * 1024 * 1024,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout,
            )

            if process.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()[:500] if stderr else ""
                log_fail("本地脚本执行", f"退出码 {process.returncode}: {err_msg}", url=url)
                return None

            markdown = stdout.decode("utf-8").strip()
            if len(markdown) <= self._min_content_length:
                log_fail("本地脚本执行", f"抓取内容过短（{len(markdown)} 字符），已丢弃", url=url)
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