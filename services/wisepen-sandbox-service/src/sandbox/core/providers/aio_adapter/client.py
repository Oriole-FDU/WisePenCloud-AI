from __future__ import annotations

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

    async def health(self) -> bool:
        url = f"{self._base_url}/v1/sandbox"
        debug(
            "AIO 健康检查请求开始",
            url=url,
            timeout_seconds=self._timeout,
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
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
                timeout_seconds=self._timeout,
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
        self, path: str, body: dict[str, Any], *, timeout: float | None = None
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
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                return payload["data"]
            return payload if isinstance(payload, dict) else {"data": payload}
        except ServiceException:
            raise
        except httpx.TimeoutException as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "AIO 请求超时",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "AIO 请求失败",
            ) from exc

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

    async def shell_exec(self, command: str, exec_dir: str, timeout_ms: int) -> dict[str, Any]:
        return await self.request(
            "/v1/shell/exec",
            # 命令执行接口在 AIO 侧的超时时间单位是秒，内部协议入口使用毫秒。
            {"command": command, "exec_dir": exec_dir, "timeout": max(1, timeout_ms // 1000)},
        )

    async def code_execute(
        self, language: str, code: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = dict(payload or {})
        body.update({"language": language, "code": code})
        return await self.request("/v1/code/execute", body)
