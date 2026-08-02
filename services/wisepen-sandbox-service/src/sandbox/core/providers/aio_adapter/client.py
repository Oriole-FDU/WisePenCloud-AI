from __future__ import annotations

from math import ceil
from typing import Any

import httpx

from common.core.exceptions import ServiceException
from common.logger import debug, error, info, warn

from sandbox.domain.error_codes import SandboxErrorCode


class AioClient:
    """all-in-one-sandbox HTTP API 的薄封装。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def health(self, *, timeout_seconds: float | None = None) -> bool:
        url = f"{self._base_url}/v1/sandbox"
        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        debug(
            "AIO 健康检查请求开始",
            url=url,
            timeout_seconds=timeout,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=self._headers())
            debug(
                "AIO 健康检查收到响应",
                url=url,
                status_code=response.status_code,
                is_success=response.is_success,
            )
            if response.is_success:
                info(
                    "AIO 健康检查成功",
                    url=url,
                    status_code=response.status_code,
                )
            else:
                warn(
                    "AIO 健康检查返回非 2xx",
                    url=url,
                    status_code=response.status_code,
                )
            return response.is_success
        except httpx.TimeoutException as exc:
            error(
                "AIO 健康检查超时",
                exc=exc,
                url=url,
                timeout_seconds=timeout,
            )
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "AIO 健康检查超时",
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "AIO 健康检查失败",
            ) from exc

    async def request(
        self,
        path: str,
        body: dict[str, Any],
        *,
        timeout: float | None = None,
        timeout_error_code: Any = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}{path}",
                    json=body,
                    headers=self._headers(),
                )
            if not response.is_success:
                if response.status_code == 404:
                    raise ServiceException(
                        SandboxErrorCode.AIO_RESOURCE_NOT_FOUND,
                        "AIO 资源不存在",
                    )
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    f"AIO 请求失败，状态码 {response.status_code}",
                )
            payload = response.json()
            # 部分 AIO 接口返回 R(data=...)，部分直接返回 dict；这里统一拆出业务 data。
            if isinstance(payload, dict) and payload.get("success") is False:
                reason = self._failure_reason(payload)
                if timeout_error_code is not None and "timeout" in reason.lower():
                    raise ServiceException(timeout_error_code, "沙箱任务执行超时")
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    f"AIO 业务请求失败：{reason}",
                )
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                return payload["data"]
            return payload if isinstance(payload, dict) else {"data": payload}
        except ServiceException:
            raise
        except httpx.TimeoutException as exc:
            raise ServiceException(
                timeout_error_code or SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "沙箱任务执行超时" if timeout_error_code else "AIO 请求超时",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "AIO 请求失败",
            ) from exc

    @staticmethod
    def _failure_reason(payload: dict[str, Any]) -> str:
        for key in ("message", "msg", "detail", "error", "code"):
            value = payload.get(key)
            if value not in (None, "", False):
                return str(value)[:500]
        return "AIO 返回 success=false 但未提供错误原因"

    async def file_read(self, path: str, max_chars: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"file": path}
        if max_chars is not None:
            body["max_chars"] = max_chars
        return await self.request("/v1/file/read", body)

    async def file_write(self, path: str, content: str) -> dict[str, Any]:
        return await self.request("/v1/file/write", {"file": path, "content": content})

    async def file_list(self, path: str, recursive: bool = False) -> dict[str, Any]:
        return await self.request("/v1/file/list", {"path": path, "recursive": recursive})

    async def file_grep(
        self, path: str, pattern: str, recursive: bool, ignore_case: bool
    ) -> dict[str, Any]:
        # 当前搜索接口使用 regex 字段，recursive 参数预留给未来协议扩展。
        return await self.request(
            "/v1/file/search",
            {
                "file": path,
                "regex": ("(?i)" if ignore_case else "") + pattern,
            },
        )

    async def file_replace(self, path: str, old_str: str, new_str: str) -> dict[str, Any]:
        return await self.request(
            "/v1/file/replace",
            {"file": path, "old_str": old_str, "new_str": new_str},
        )

    async def shell_exec(
        self,
        command: str,
        exec_dir: str,
        timeout_ms: int,
        request_grace_seconds: float,
    ) -> dict[str, Any]:
        timeout_seconds = max(1, ceil(timeout_ms / 1000))
        request_timeout_seconds = timeout_seconds + request_grace_seconds
        debug(
            "AIO Shell 执行超时预算已解析",
            timeout_ms=timeout_ms,
            aio_timeout_seconds=timeout_seconds,
            request_timeout_seconds=request_timeout_seconds,
        )
        result = await self.request(
            "/v1/shell/exec",
            {"command": command, "exec_dir": exec_dir, "timeout": timeout_seconds},
            timeout=request_timeout_seconds,
            timeout_error_code=SandboxErrorCode.EXECUTION_TIMEOUT,
        )
        if str(result.get("status", "")).lower() != "running":
            return result

        session_id = str(result.get("session_id") or "").strip()
        if session_id:
            try:
                await self.request(
                    "/v1/shell/kill",
                    {"id": session_id},
                    timeout=max(1.0, request_grace_seconds),
                )
            except ServiceException as exc:
                error(
                    "AIO Shell 超时后的进程终止失败",
                    exc=exc,
                    session_id=session_id,
                )
        raise ServiceException(
            SandboxErrorCode.EXECUTION_TIMEOUT,
            "沙箱任务执行超时",
        )

    async def code_execute(
        self,
        language: str,
        code: str,
        timeout_ms: int,
        request_grace_seconds: float,
    ) -> dict[str, Any]:
        timeout_seconds = max(1, ceil(timeout_ms / 1000))
        request_timeout_seconds = timeout_seconds + request_grace_seconds
        debug(
            "AIO 代码执行超时预算已解析",
            language=language,
            timeout_ms=timeout_ms,
            aio_timeout_seconds=timeout_seconds,
            request_timeout_seconds=request_timeout_seconds,
        )
        return await self.request(
            "/v1/code/execute",
            {"language": language, "code": code, "timeout": timeout_seconds},
            timeout=request_timeout_seconds,
            timeout_error_code=SandboxErrorCode.EXECUTION_TIMEOUT,
        )
