from __future__ import annotations

from typing import Any

import httpx

from sandbox.core.providers.aio_adapter.errors import AioRequestError


class AioClient:
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
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base_url}/v1/sandbox", headers=self._headers()
                )
            return response.is_success
        except httpx.TimeoutException as exc:
            raise AioRequestError("AIO health check timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AioRequestError("AIO health check failed", retryable=True) from exc

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
                    from sandbox.core.providers.aio_adapter.errors import AioNotFoundError

                    raise AioNotFoundError("AIO resource was not found")
                raise AioRequestError(
                    f"AIO request failed with status {response.status_code}",
                    retryable=response.status_code >= 500,
                )
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                return payload["data"]
            return payload if isinstance(payload, dict) else {"data": payload}
        except AioRequestError:
            raise
        except httpx.TimeoutException as exc:
            raise AioRequestError("AIO request timed out", retryable=True) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AioRequestError("AIO request failed", retryable=True) from exc

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
            {"command": command, "exec_dir": exec_dir, "timeout": max(1, timeout_ms // 1000)},
        )

    async def code_execute(
        self, language: str, code: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = dict(payload or {})
        body.update({"language": language, "code": code})
        return await self.request("/v1/code/execute", body)
