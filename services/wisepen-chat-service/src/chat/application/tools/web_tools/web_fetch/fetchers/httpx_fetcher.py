from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

import httpx

from chat.application.tools.utils.file_type_detect import detect_file_type_from_bytes
from common.logger import info, warn
from .base import RawFetchOutput
from ..errors import WebFetchHttpError, WebFetchNetworkError, WebFetchUnsupportedUrlError
from ..utils import decode_bytes, filename_from_url

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_SNIFF_BUFFER_BYTES = 32_768  # 32 KiB
_STREAM_CHUNK_SIZE = 65_536   # 64 KiB


class HttpxFetcher:
    """httpx 静态抓取器。

    流式嗅探前 32KB 判断文件类型：
    - HTML：继续流式读完整，解码为 raw_html
    - 非 HTML：写临时文件完整落盘，返回 file_path

    不可恢复错误（HTTP 4xx/5xx、网络失败、URL 不支持）直接 raise WebFetchError。
    """

    __slots__ = ("_http", "_max_response_bytes")

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        max_response_bytes: int = 52_428_800,
    ) -> None:
        self._http = http_client
        self._max_response_bytes = max_response_bytes

    @property
    def name(self) -> str:
        return "httpx"

    async def fetch(self, url: str) -> RawFetchOutput:
        if not _URL_SCHEME_RE.match(url.strip()):
            raise WebFetchUnsupportedUrlError(
                url=url,
                reason="unsupported url scheme, only http/https allowed",
            )

        try:
            async with self._http.stream(
                "GET",
                url,
                headers=_DEFAULT_HEADERS,
                follow_redirects=True,
            ) as response:
                if response.status_code >= 400:
                    raise WebFetchHttpError(url=url, reason=f"http {response.status_code}")

                content_type = response.headers.get("content-type")
                final_url = str(response.url)
                headers = dict(response.headers)

                stream = response.aiter_bytes(chunk_size=_STREAM_CHUNK_SIZE)
                sniff_buffer = await _read_sniff_from_stream(stream, _SNIFF_BUFFER_BYTES)
                file_type = detect_file_type_from_bytes(
                    sniff_buffer,
                    fallback_name=filename_from_url(final_url),
                )

                # 嗅探已消耗的字节不计入后续预算
                budget = self._max_response_bytes - len(sniff_buffer)

                # 两条路径共享的不变字段
                base = dict(
                    source_url=url,
                    fetcher=self.name,
                    final_url=final_url,
                    status_code=response.status_code,
                    content_type=content_type,
                    headers=headers,
                )

                if file_type.label == "html":
                    remaining = await _drain_bounded(stream, budget, url)
                    return RawFetchOutput(
                        **base,
                        raw_html=decode_bytes(sniff_buffer + remaining, response.charset_encoding),
                    )

                file_path = await _write_to_temp_file(
                    stream, sniff_buffer, budget, url, file_type.label
                )
                info(
                    "web_fetch httpx non-html file saved",
                    url=url,
                    file_path=file_path,
                    label=file_type.label,
                )
                return RawFetchOutput(**base, file_path=file_path, file_label=file_type.label)

        except (WebFetchHttpError, WebFetchNetworkError, WebFetchUnsupportedUrlError):
            raise
        except httpx.TimeoutException as exc:
            raise WebFetchNetworkError(url=url, reason=f"timeout: {exc}") from exc
        except httpx.NetworkError as exc:
            raise WebFetchNetworkError(url=url, reason=f"network: {exc}") from exc
        except httpx.HTTPError as exc:
            raise WebFetchNetworkError(url=url, reason=f"http: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise WebFetchNetworkError(url=url, reason=f"unexpected: {exc}") from exc


async def _read_sniff_from_stream(
    stream: AsyncIterator[bytes],
    max_bytes: int,
) -> bytes:
    """从流中读取最多 max_bytes 的嗅探缓冲区，不消费整个流。"""
    chunks: list[bytes] = []
    total = 0
    async for chunk in stream:
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    return b"".join(chunks)[:max_bytes]  # 直接 slice，无需条件判断


async def _bounded_stream(
    stream: AsyncIterator[bytes],
    budget: int,
    url: str,
) -> AsyncGenerator[bytes, None]:
    """逐块 yield 流内容；预算耗尽时 warn + raise。

    被 _drain_bounded 和 _write_to_temp_file 共用，消除重复的大小追踪逻辑。
    """
    remaining = budget
    async for chunk in stream:
        remaining -= len(chunk)
        if remaining < 0:
            warn("web_fetch response exceeded max bytes", url=url, max_bytes=budget)
            raise WebFetchNetworkError(url=url, reason=f"response exceeded max bytes {budget}")
        yield chunk


async def _drain_bounded(
    stream: AsyncIterator[bytes],
    budget: int,
    url: str,
) -> bytes:
    """将流剩余内容读入内存并返回，超出 budget 时 raise。"""
    return b"".join([chunk async for chunk in _bounded_stream(stream, budget, url)])


async def _write_to_temp_file(
    stream: AsyncIterator[bytes],
    sniff_buffer: bytes,
    budget: int,
    url: str,
    label: str,
) -> str:
    """将完整响应写入临时文件，返回路径；超出 budget 或写入失败时清理并 raise。"""
    fd, tmp_path = tempfile.mkstemp(prefix="web_fetch_", suffix=f".{label}" if label else "")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(sniff_buffer)
            async for chunk in _bounded_stream(stream, budget, url):
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        return tmp_path
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink(missing_ok=True)
        raise