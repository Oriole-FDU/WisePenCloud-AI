from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
import hashlib
import secrets
import string
from typing import Any, BinaryIO, TypeVar

import httpx
import websockets
from pydantic import BaseModel

from sandbox.domain.entities import CommandTaskStatus
from sandbox.domain.interfaces import (
    BrowserActionResult,
    BrowserChannel,
    BrowserContentResult,
    BrowserInfoResult,
    BrowserListResult,
    BrowserViewport,
    FileContentEncoding,
    FileDownloadResult,
    FileFindResult,
    FileGlobResult,
    FileGlobSortField,
    FileGrepResult,
    FileListResult,
    FileListSortField,
    FileReadResult,
    FileReplaceResult,
    FileSearchResult,
    FileTransferTooLargeError,
    FileUploadResult,
    FileWriteResult,
    ProviderCommandHandle,
    ProviderCommandSnapshot,
    SandboxProvision,
    SandboxProvider,
    SandboxProviderType,
)


FileResultT = TypeVar("FileResultT", bound=BaseModel)


class AIOAdapter(SandboxProvider):
    """AIO 沙箱 Provider 适配器"""

    _HEALTH_FIELDS = frozenset({"success", "message", "data", "home_dir", "version", "detail"})

    def __init__(
        self,
        image: str,
        request_timeout_seconds: float,
        file_transfer_timeout_seconds: float = 120.0,
        browser_request_timeout_seconds: float = 60.0,
    ) -> None:
        self._image = image
        self._request_timeout_seconds = request_timeout_seconds
        self._file_transfer_timeout_seconds = file_transfer_timeout_seconds
        self._browser_request_timeout_seconds = browser_request_timeout_seconds

    @classmethod
    def sandbox_provider_id(cls) -> SandboxProviderType:
        return SandboxProviderType.AIO

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> AIOAdapter:
        return cls(
            image=config.get("image"),
            request_timeout_seconds=config.get("request_timeout_seconds", 5.0),
            file_transfer_timeout_seconds=config.get("file_transfer_timeout_seconds", 120.0),
            browser_request_timeout_seconds=config.get("browser_request_timeout_seconds", 60.0),
        )

    def create_provision(self) -> SandboxProvision:
        """生成 AIO 专属的容器环境变量和运行时认证元数据"""
        alphabet = string.ascii_letters + string.digits
        api_key = "".join(secrets.choice(alphabet) for _ in range(48))
        return SandboxProvision(
            image=self._image,
            container_environment={"SANDBOX_API_KEY": api_key},
            provider_metadata={"api_key": api_key},
        )

    @staticmethod
    def _headers(provider_metadata: Mapping[str, object] | None) -> dict[str, str]:
        api_key = (provider_metadata or {}).get("api_key")
        return {"X-AIO-API-Key": str(api_key)} if api_key else {}

    async def _request(
        self,
        method: str,
        base_url: str,
        endpoint: str,
        *,
        json: Mapping[str, object | None] | None = None,
        params: Mapping[str, object | None] | None = None,
        timeout: float | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        """统一的非流式请求入口：构造带认证头的客户端并返回响应，状态码与解析由调用方处理。"""
        request_kwargs: dict[str, Any] = {}
        if json is not None:
            request_kwargs["json"] = {key: value for key, value in json.items() if value is not None}
        if params is not None:
            request_kwargs["params"] = {key: value for key, value in params.items() if value is not None}
        async with httpx.AsyncClient(
            timeout=timeout or self._request_timeout_seconds,
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            return await client.request(
                method,
                f"{base_url.rstrip('/')}{endpoint}",
                **request_kwargs,
            )

    async def check_ready(
        self,
        base_url: str | None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        # 预热阶段通过 AIO 健康接口确认容器内服务已经可以接受命令
        if base_url is None:
            return False
        try:
            response = await self._request("GET", base_url, "/v1/sandbox", provider_metadata=provider_metadata)
            if response.status_code != 200:
                return False
            payload = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return False
        return isinstance(payload, dict) and self._HEALTH_FIELDS.issubset(payload)

    # ------------------------------------------------------------------
    # Bash 能力（/v1/bash/*）
    # ------------------------------------------------------------------

    async def start_command(
        self,
        base_url: str,
        command: str,
        working_directory: str,
        timeout_seconds: int,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandHandle:
        """通过 AIO Bash Pipe 启动命令并返回 Provider 句柄"""
        response = await self._request(
            "POST", base_url, "/v1/bash/exec",
            json={
                "command": command,
                "exec_dir": working_directory,
                "async_mode": True,
                "hard_timeout": timeout_seconds,
            },
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "bash")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("AIO Bash returned an invalid command handle")
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("AIO Bash did not return a command session id")
        command_id = data.get("command_id")
        return ProviderCommandHandle(
            provider_session_id=session_id,
            provider_command_id=command_id if isinstance(command_id, str) else None,
        )

    async def get_command_result(
        self,
        base_url: str,
        handle: ProviderCommandHandle,
        stdout_offset: int,
        stderr_offset: int,
        wait_timeout_seconds: float,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandSnapshot:
        """读取 AIO 命令的增量输出和状态"""
        response = await self._request(
            "POST", base_url, "/v1/bash/output",
            json={
                "session_id": handle.provider_session_id,
                "command_id": handle.provider_command_id,
                "offset": stdout_offset,
                "stderr_offset": stderr_offset,
                "wait": True,
                "wait_timeout": wait_timeout_seconds,
            },
            timeout=max(self._request_timeout_seconds, wait_timeout_seconds + 2),
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "bash")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("AIO Bash returned an invalid command result")
        stdout = data.get("stdout") or ""
        stderr = data.get("stderr") or ""
        command = data.get("command") or {}
        return ProviderCommandSnapshot(
            status=CommandTaskStatus(command.get("status")),
            exit_code=command.get("exit_code"),
            stdout=stdout,
            stderr=stderr,
            stdout_offset=int(data.get("offset") or stdout_offset + len(stdout.encode("utf-8"))),
            stderr_offset=int(data.get("stderr_offset") or stderr_offset + len(stderr.encode("utf-8"))),
        )

    async def kill_command(
        self,
        base_url: str,
        handle: ProviderCommandHandle,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """主动终止 AIO 命令"""
        response = await self._request(
            "POST", base_url, "/v1/bash/kill",
            json={"session_id": handle.provider_session_id, "signal": "SIGKILL"},
            provider_metadata=provider_metadata,
        )
        self._parse_envelope(response, "bash")


    # ------------------------------------------------------------------
    # File 能力（/v1/file/*）
    # ------------------------------------------------------------------

    async def read_file(
        self,
        base_url: str,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReadResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/read",
            {
                "file": path,
                "start_line": start_line,
                "end_line": end_line,
                "sudo": False,
            },
            FileReadResult,
            provider_metadata,
        )

    async def write_file(
        self,
        base_url: str,
        path: str,
        content: str,
        *,
        encoding: FileContentEncoding = FileContentEncoding.UTF8,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileWriteResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/write",
            {
                "file": path,
                "content": content,
                "encoding": encoding.value,
                "append": append,
                "leading_newline": leading_newline,
                "trailing_newline": trailing_newline,
                "sudo": False,
            },
            FileWriteResult,
            provider_metadata,
        )

    async def replace_in_file(
        self,
        base_url: str,
        path: str,
        old_str: str,
        new_str: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReplaceResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/replace",
            {"file": path, "old_str": old_str, "new_str": new_str, "sudo": False},
            FileReplaceResult,
            provider_metadata,
        )

    async def search_in_file(
        self,
        base_url: str,
        path: str,
        regex: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileSearchResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/search",
            {"file": path, "regex": regex, "sudo": False},
            FileSearchResult,
            provider_metadata,
        )

    async def find_files(
        self,
        base_url: str,
        path: str,
        glob: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileFindResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/find",
            {"path": path, "glob": glob},
            FileFindResult,
            provider_metadata,
        )

    async def grep_files(
        self,
        base_url: str,
        path: str,
        pattern: str,
        *,
        include: tuple[str, ...] | None = None,
        exclude: tuple[str, ...] | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        context_before: int = 0,
        context_after: int = 0,
        max_results: int = 500,
        max_file_size: str = "1M",
        multiline: bool = False,
        offset: int = 0,
        file_type: str | None = None,
        recursive: bool = True,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileGrepResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/grep",
            {
                "path": path,
                "pattern": pattern,
                "include": include,
                "exclude": exclude,
                "case_insensitive": case_insensitive,
                "fixed_strings": fixed_strings,
                "context_before": context_before,
                "context_after": context_after,
                "max_results": max_results,
                "max_file_size": max_file_size,
                "multiline": multiline,
                "offset": offset,
                "type": file_type,
                "recursive": recursive,
            },
            FileGrepResult,
            provider_metadata,
        )

    async def glob_files(
        self,
        base_url: str,
        path: str,
        pattern: str,
        *,
        exclude: tuple[str, ...] | None = None,
        include_hidden: bool = False,
        files_only: bool = True,
        include_metadata: bool = True,
        max_results: int = 5000,
        sort_by: FileGlobSortField = FileGlobSortField.PATH,
        sort_desc: bool = False,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileGlobResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/glob",
            {
                "path": path,
                "pattern": pattern,
                "exclude": exclude,
                "include_hidden": include_hidden,
                "files_only": files_only,
                "include_metadata": include_metadata,
                "max_results": max_results,
                "sort_by": sort_by.value,
                "sort_desc": sort_desc,
            },
            FileGlobResult,
            provider_metadata,
        )

    async def list_files(
        self,
        base_url: str,
        path: str,
        *,
        recursive: bool = False,
        show_hidden: bool = True,
        file_types: tuple[str, ...] | None = None,
        max_depth: int | None = None,
        include_size: bool = True,
        include_permissions: bool = False,
        sort_by: FileListSortField = FileListSortField.NAME,
        sort_desc: bool = False,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileListResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/list",
            {
                "path": path,
                "recursive": recursive,
                "show_hidden": show_hidden,
                "file_types": file_types,
                "max_depth": max_depth,
                "include_size": include_size,
                "include_permissions": include_permissions,
                "sort_by": sort_by.value,
                "sort_desc": sort_desc,
            },
            FileListResult,
            provider_metadata,
        )

    async def upload_file(
        self,
        base_url: str,
        path: str,
        source: BinaryIO,
        filename: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileUploadResult:
        source.seek(0)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._file_transfer_timeout_seconds),
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/file/upload",
                data={"path": path},
                files={"file": (filename, source, "application/octet-stream")},
            )
        payload = self._parse_envelope(response, "file")
        return FileUploadResult.model_validate(payload.get("data"))

    async def download_file(
        self,
        base_url: str,
        path: str,
        destination: BinaryIO,
        max_bytes: int,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileDownloadResult:
        total = 0
        digest = hashlib.md5()
        timeout = httpx.Timeout(self._file_transfer_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            async with client.stream(
                "GET",
                f"{base_url.rstrip('/')}/v1/file/download",
                params={"path": path, "change_policy": "abort"},
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                try:
                    declared_size = int(content_length) if content_length else None
                except ValueError:
                    declared_size = None
                if declared_size is not None and declared_size > max_bytes:
                    raise FileTransferTooLargeError(max_bytes)
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FileTransferTooLargeError(max_bytes)
                    digest.update(chunk)
                    destination.write(chunk)
        destination.seek(0)
        return FileDownloadResult(size=total, md5=digest.hexdigest())

    async def _post_file_result(
        self,
        base_url: str,
        endpoint: str,
        request_data: Mapping[str, object | None],
        result_type: type[FileResultT],
        provider_metadata: Mapping[str, object] | None,
    ) -> FileResultT:
        response = await self._request(
            "POST", base_url, endpoint, json=request_data, provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "file")
        return result_type.model_validate(payload.get("data"))

    @staticmethod
    def _parse_envelope(response: httpx.Response, scope: str) -> dict[str, Any]:
        """校验统一响应信封 {success, message, data}，返回原始 payload dict。

        scope 仅用于错误消息（"file"/"browser"/"bash"）。data 的形状因端点而异，
        因此这里只校验到信封边界，data 的解析留给调用方。
        """
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"AIO {scope} API returned non-JSON data") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"AIO {scope} API returned an invalid response")
        if payload.get("success") is not True:
            raise RuntimeError(
                f"AIO {scope} operation failed: {payload.get('message') or 'invalid response'}"
            )
        return payload

    # ------------------------------------------------------------------
    # Browser 能力（/v1/browser/*）
    # ------------------------------------------------------------------

    @staticmethod
    def _browser_action_result(payload: Mapping[str, Any]) -> BrowserActionResult:
        return BrowserActionResult.model_validate(payload)

    @staticmethod
    def _browser_content_result(payload: Mapping[str, Any]) -> BrowserContentResult:
        data = payload.get("data")
        if isinstance(data, str):
            content = data
        elif isinstance(data, dict) and data.get("markdown") is not None:
            content = str(data["markdown"])
        else:
            content = str(data) if data is not None else ""
        return BrowserContentResult(content=content, raw=data)

    @staticmethod
    def _browser_list_result(payload: Mapping[str, Any]) -> BrowserListResult:
        data = payload.get("data")
        return BrowserListResult(items=data if isinstance(data, list) else [], raw=data)

    async def get_info(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserInfoResult:
        response = await self._request(
            "GET", base_url, "/v1/browser/info",
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        viewport = data.get("viewport") or {}
        page_viewport = data.get("page_viewport")
        return BrowserInfoResult(
            user_agent=data.get("user_agent", ""),
            cdp_url=data.get("cdp_url", ""),
            vnc_url=data.get("vnc_url"),
            cdp_ui_url=data.get("cdp_ui_url"),
            viewport=BrowserViewport(
                width=viewport.get("width", 0) if isinstance(viewport, dict) else 0,
                height=viewport.get("height", 0) if isinstance(viewport, dict) else 0,
            ),
            page_viewport=BrowserViewport(
                width=page_viewport.get("width", 0),
                height=page_viewport.get("height", 0),
            ) if isinstance(page_viewport, dict) else None,
        )

    @asynccontextmanager
    async def connect_cdp(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> AsyncIterator[BrowserChannel]:
        """建立到浏览器 CDP 的双向通道：解析 cdp_url 并携带鉴权头连接上游。"""
        info = await self.get_info(base_url, provider_metadata)
        async with websockets.connect(
            info.cdp_url,
            extra_headers=self._headers(provider_metadata),
        ) as ws:
            yield ws

    async def screenshot(
        self,
        base_url: str,
        destination: BinaryIO,
        *,
        format: str = "png",
        quality: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> None:
        params: dict[str, object] = {"format": format}
        if quality is not None:
            params["quality"] = quality
        timeout = httpx.Timeout(self._browser_request_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            async with client.stream(
                "GET",
                f"{base_url.rstrip('/')}/v1/browser/screenshot",
                params=params,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    destination.write(chunk)
        destination.seek(0)

    async def execute_action(
        self,
        base_url: str,
        action: Mapping[str, Any],
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/actions",
            json=dict(action),
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def navigate(
        self,
        base_url: str,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/navigate",
            json={"url": url, "wait_until": wait_until, "timeout": timeout},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def back(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/back",
            json={},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def forward(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/forward",
            json={},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def reload(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/reload",
            json={},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def click(
        self,
        base_url: str,
        *,
        selector: str | None = None,
        index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str | None = None,
        click_count: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/click",
            json={"selector": selector, "index": index, "x": x, "y": y, "button": button, "click_count": click_count},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def fill(
        self,
        base_url: str,
        text: str,
        *,
        selector: str | None = None,
        index: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/fill",
            json={"selector": selector, "index": index, "text": text},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def press_key(
        self,
        base_url: str,
        key: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/press_key",
            json={"key": key},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def hot_key(
        self,
        base_url: str,
        keys: list[str],
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/hot_key",
            json={"keys": keys},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def hover(
        self,
        base_url: str,
        *,
        selector: str | None = None,
        x: float | None = None,
        y: float | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/hover",
            json={"selector": selector, "x": x, "y": y},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def scroll(
        self,
        base_url: str,
        *,
        direction: str | None = None,
        amount: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/scroll",
            json={"direction": direction, "amount": amount},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def evaluate(
        self,
        base_url: str,
        expression: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/page/evaluate",
            json={"expression": expression},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def get_markdown(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserContentResult:
        response = await self._request(
            "GET", base_url, "/v1/browser/page/markdown",
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_content_result(payload)

    async def get_text(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserContentResult:
        response = await self._request(
            "GET", base_url, "/v1/browser/page/text",
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_content_result(payload)

    async def get_elements(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserListResult:
        response = await self._request(
            "GET", base_url, "/v1/browser/page/elements",
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_list_result(payload)

    async def list_tabs(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserListResult:
        response = await self._request(
            "GET", base_url, "/v1/browser/tabs",
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_list_result(payload)

    async def create_tab(
        self,
        base_url: str,
        url: str | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/tabs",
            json={"url": url},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def close_tab(
        self,
        base_url: str,
        index: int,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "DELETE", base_url, f"/v1/browser/tabs/{index}",
            json={},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def activate_tab(
        self,
        base_url: str,
        index: int,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "PUT", base_url, f"/v1/browser/tabs/{index}/activate",
            json={},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def get_cookies(
        self,
        base_url: str,
        *,
        urls: str | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserListResult:
        response = await self._request(
            "GET", base_url, "/v1/browser/cookies",
            params={"urls": urls},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_list_result(payload)

    async def set_cookies(
        self,
        base_url: str,
        cookies: list[Mapping[str, Any]],
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/cookies",
            json={"cookies": cookies},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def clear_cookies(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "DELETE", base_url, "/v1/browser/cookies",
            json={},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def save_state(
        self,
        base_url: str,
        path: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/state/save",
            json={"path": path},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)

    async def load_state(
        self,
        base_url: str,
        path: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        response = await self._request(
            "POST", base_url, "/v1/browser/state/load",
            json={"path": path},
            timeout=self._browser_request_timeout_seconds,
            provider_metadata=provider_metadata,
        )
        payload = self._parse_envelope(response, "browser")
        return self._browser_action_result(payload)
