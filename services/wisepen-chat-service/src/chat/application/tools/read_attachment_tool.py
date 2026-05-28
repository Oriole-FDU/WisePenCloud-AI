import re
from abc import ABC
from io import BytesIO
from typing import Dict, Any, Optional, Tuple

import httpx

from common.logger import log_error
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from chat.domain.repositories import SessionRepository
from common.clients.file_storage import FileStorageClient


class BaseReadAttachmentTool(BaseTool, ABC):
    """附件读取工具基类：统一下载、鉴权、截断。"""

    def __init__(
        self,
        file_storage_client: FileStorageClient,
        session_repo: SessionRepository,
    ) -> None:
        self._file_storage = file_storage_client
        self._session_repo = session_repo

    async def _validate_and_download(
        self, context: Dict[str, Any], object_key: str
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """鉴权并下载附件原始字节。返回 (content, error)。"""
        session_id: str = context.get("session_id", "")
        user_id: str = context.get("user_id", "")

        session = await self._session_repo.get_session_for_user(session_id, user_id)
        matched = any(a.object_key == object_key for a in session.attachments)
        if not matched:
            return None, f"[Tool Error] Attachment not found: '{object_key}'."

        try:
            download_url = await self._file_storage.get_download_url(object_key)
        except Exception as e:
            log_error("获取附件下载链接失败", e, object_key=object_key)
            return None, f"[Tool Error] Failed to get download URL: {e}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                resp = await client.get(download_url)
                resp.raise_for_status()
                return resp.content, None
        except Exception as e:
            log_error("下载附件内容失败", e, object_key=object_key)
            return None, f"[Tool Error] Failed to download attachment content: {e}"

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) > settings.TOOL_RESULT_MAX_CHARS:
            return text[:settings.TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"
        return text

    @staticmethod
    def _resolve_object_key(kwargs: Dict[str, Any]) -> Optional[str]:
        object_key: str = kwargs.get("object_key", "").strip()
        return object_key if object_key else None

    @staticmethod
    def _extract_ole2_text(data: bytes, stream_names: list[str]) -> str:
        """从 OLE2 复合文档中提取文本（用于 .doc .ppt 等旧格式兜底）。

        扫描指定 stream 中的可打印字符序列（含中文字符），拼接为粗略文本。
        """
        try:
            import olefile
        except ImportError:
            return ""

        try:
            ole = olefile.OleFileIO(BytesIO(data))
        except Exception:
            return ""

        chunks: list[str] = []
        for name in stream_names:
            if not ole.exists(name):
                continue
            try:
                raw = ole.openstream(name).read()
            except Exception:
                continue
            # 提取 UTF-16LE 编码的可打印文本段
            try:
                decoded = raw.decode("utf-16-le", errors="ignore")
            except Exception:
                decoded = raw.decode("latin-1", errors="ignore")
            # 保留字母、数字、中文、常见标点，过滤控制字符
            runs = re.findall(r"[\w一-鿿　-〿＀-￯.,;:!?()\[\]{}\"\'\-+/=@#$%^&*|\\<>~` ]{4,}", decoded)
            if runs:
                chunks.append("\n".join(runs))

        ole.close()
        return "\n".join(chunks)
