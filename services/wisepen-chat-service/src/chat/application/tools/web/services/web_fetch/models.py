from dataclasses import dataclass
from typing import List, Optional

from chat.application.tools.web.services.web_fetch.enums import FetcherName


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """表示抓取到的文档类二进制内容（如 PDF、Word 等）。"""
    url: str
    media_type: str
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class FetchedLink:
    """表示从页面中提取的超链接，包含目标 URL、锚文本和周围上下文。"""
    url: str
    anchor_text: str = ""
    surrounding_text: str = ""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """表示抓取并处理后的页面结果，包含 Markdown 内容及元信息。"""
    markdown: str
    links: List[FetchedLink]
    title: Optional[str] = None
    final_url: Optional[str] = None
    domain: Optional[str] = None
    status_code: Optional[int] = None


@dataclass(frozen=True, slots=True)
class FetchedRedirect:
    """表示跨站重定向信息，包含原始 URL、目标 URL 和状态码。"""
    url: str
    redirect_url: str
    status_code: Optional[int] = None


@dataclass(frozen=True, slots=True)
class FetchResultItem:
    """表示单个 URL 的完整抓取结果，包含成功状态、内容及错误信息。"""
    url: str
    success: bool
    content: Optional[str] = None
    document: Optional[FetchedDocument] = None
    links: Optional[List[FetchedLink]] = None
    title: Optional[str] = None
    final_url: Optional[str] = None
    domain: Optional[str] = None
    status_code: Optional[int] = None
    redirect_url: Optional[str] = None
    error: Optional[str] = None
    fetcher: Optional[FetcherName] = None