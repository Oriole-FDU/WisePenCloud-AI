import re
from typing import List, Set

import httpx
from common.clients.file_storage import FileStorageClient

from chat.domain.interfaces.attachment_auditor import AttachmentAuditor, AttachmentAuditResult


_HARMFUL_PATTERNS: List[re.Pattern] = [
    re.compile(r"[\s\S]{0,10}malware[\s\S]{0,10}", re.IGNORECASE),
    re.compile(r"[\s\S]{0,10}ransomware[\s\S]{0,10}", re.IGNORECASE),
    re.compile(r"[\s\S]{0,10}exploit[\s\S]{0,10}", re.IGNORECASE),
    re.compile(r"[\s\S]{0,10}keylogger[\s\S]{0,10}", re.IGNORECASE),
]

_IMAGE_EXTENSIONS: Set[str] = {"png", "jpg", "jpeg"}
_IMAGE_MAGIC: Set[bytes] = {b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"}

_MAX_CHARS_CHECK = 500_000


class SimpleAttachmentAuditor(AttachmentAuditor):
    """基于文件头校验和关键词匹配的本地附件审计器"""

    def __init__(
        self,
        file_storage_client: FileStorageClient,
        enabled: bool = True,
    ):
        self._file_storage_client = file_storage_client
        self._enabled = enabled
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def audit(
        self,
        object_key: str,
        extension: str,
        extracted_text: str = "",
    ) -> AttachmentAuditResult:
        if not self._enabled:
            return AttachmentAuditResult(passed=True)

        if extension in _IMAGE_EXTENSIONS:
            return await self._audit_image(object_key, extension)

        return await self._audit_document_text(extracted_text)

    async def _audit_image(
        self,
        object_key: str,
        extension: str,
    ) -> AttachmentAuditResult:
        download_url = await self._file_storage_client.get_download_url(object_key)
        try:
            resp = await self._http.get(download_url)
            resp.raise_for_status()
        except Exception as exc:
            return AttachmentAuditResult(passed=False, reason=f"文件下载失败，无法完成安全审核: {exc}")
        body = resp.content

        if len(body) == 0:
            return AttachmentAuditResult(passed=False, reason="图像文件为空，拒绝入库")

        matched = _IMAGE_MAGIC.copy()
        ext = extension.lower()
        if ext == "png":
            matched.discard(b"\xff\xd8\xff")
        elif ext in {"jpg", "jpeg"}:
            matched.discard(b"\x89PNG\r\n\x1a\n")

        if not any(body.startswith(m) for m in matched):
            return AttachmentAuditResult(
                passed=False,
                reason=f"图像文件格式校验失败（扩展名 {extension} 与实际内容不匹配）",
            )

        return AttachmentAuditResult(passed=True)

    async def _audit_document_text(self, text: str) -> AttachmentAuditResult:
        if not text:
            return AttachmentAuditResult(passed=True)

        chunk = text[:_MAX_CHARS_CHECK]
        for pattern in _HARMFUL_PATTERNS:
            match = pattern.search(chunk)
            if match:
                return AttachmentAuditResult(
                    passed=False,
                    reason=f"文档内容包含疑似有害关键词（命中规则: {pattern.pattern}）",
                )

        return AttachmentAuditResult(passed=True)
