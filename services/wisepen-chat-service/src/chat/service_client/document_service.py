from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict

import httpx

from common.core.constants import SecurityConstants
from common.http.rpc_client import RpcClient
from common.logger import logger


@dataclass
class DocumentUploadResult:
    document_id: str
    flash_uploaded: bool = False


class DocumentServiceClient:
    """Java document-service 的 Python 客户端。

    封装：MD5 计算 → POST /document/uploadDoc → OSS PUT → 返回 documentId。
    """

    def __init__(self, rpc: RpcClient) -> None:
        self._rpc = rpc

    async def upload(
        self,
        content: bytes,
        filename: str,
        user_id: str,
    ) -> DocumentUploadResult:
        md5 = hashlib.md5(content).hexdigest()
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        file_size = len(content)

        data: Dict[str, Any] = await self._rpc.post(
            "wisepen-document-service",
            "/document/uploadDoc",
            json={
                "filename": filename,
                "extension": ext,
                "md5": md5,
                "expectedSize": file_size,
            },
            headers={
                SecurityConstants.HEADER_USER_ID: user_id,
            },
        )

        document_id: str = data["documentId"]
        flash_uploaded: bool = data.get("flashUploaded", False)

        if not flash_uploaded:
            put_url: str = data["putUrl"]
            callback_header: str = data.get("callbackHeader", "")
            headers: Dict[str, str] = {}
            if callback_header:
                headers["x-oss-callback"] = callback_header

            async with httpx.AsyncClient() as client:
                resp = await client.put(put_url, content=content, headers=headers)
                resp.raise_for_status()

        logger.info(
            f"document uploaded documentId={document_id} "
            f"filename=\"{filename}\" fileSize={file_size} flashUploaded={flash_uploaded}"
        )
        return DocumentUploadResult(document_id=document_id, flash_uploaded=flash_uploaded)
