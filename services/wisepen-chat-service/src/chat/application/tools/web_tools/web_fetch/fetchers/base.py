from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RawFetchOutput:
    """fetcher 层输出：原始抓取结果，未清洗。

    fetcher 层失败时直接 raise WebFetchError，不返回此对象。
    此对象只在抓取成功（HTTP 2xx 且拿到响应体）时返回。

    两种互斥结果：
    - HTML 页面：raw_html 有值，file_path 为 None
    - 非 HTML 文件：file_path 有值（临时文件路径），raw_html 为 None

    字段：
    - source_url / final_url: 原始请求与最终落地 URL
    - status_code / content_type / headers: HTTP 元信息
    - fetcher: 抓取器名称（httpx | scrapling）
    - raw_html: 原始 HTML 文本（HTML 页面时有值，非 HTML 时为 None）
    - file_path: 非 HTML 文件的临时落盘路径（非 HTML 时有值，HTML 时为 None）
    - file_label: magika 检测的文件类型 label（如 "pdf"、"docx"），非 HTML 时有值
    """

    source_url: str
    fetcher: str
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    raw_html: str | None = None
    file_path: str | None = None
    file_label: str | None = None


class BaseFetcher(Protocol):
    """fetcher 协议。

    fetcher 负责"拿内容"（HTML 或非 HTML 文件），不负责清洗。
    不可恢复错误（HTTP 4xx/5xx、网络失败、URL 不支持）直接 raise WebFetchError。
    成功返回 RawFetchOutput。
    """

    @property
    def name(self) -> str:
        """抓取器名称，用于 RawFetchOutput.fetcher 字段。"""
        ...

    async def fetch(self, url: str) -> RawFetchOutput:
        """抓取单个 URL。

        成功返回 RawFetchOutput；不可恢复错误 raise WebFetchError。
        """
        ...
