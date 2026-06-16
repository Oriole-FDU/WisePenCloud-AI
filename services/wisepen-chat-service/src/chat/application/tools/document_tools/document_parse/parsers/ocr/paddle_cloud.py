from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
import httpx

from chat.application.tools.document_tools.document_parse.errors import PrimaryParserError
from chat.application.tools.document_tools.document_parse.models import (
    DocumentParseMonitorName,
    OcrPageResult,
)
from chat.application.tools.utils.markdown_renderer import (
    FragmentMarkdownRenderer,
    TableMarkdownRenderer,
)


@dataclass(frozen=True, slots=True)
class PaddleCloudPPStructureV3Config:
    api_url: str  # PaddleOCR 云端接口地址
    token: str  # PaddleOCR 鉴权 token
    timeout_seconds: float = 60.0  # 单次请求超时
    retries: int = 2  # 失败后的额外重试次数
    optional_payload: Mapping[str, Any] = field(
        default_factory=lambda: {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "useChartRecognition": False,
            "useTableRecognition": True,
            "useFormulaRecognition": True,
            "useRegionDetection": True,
        }
    )


class PaddleCloudPPStructureV3Client:
    """PaddleOCR 云端 PP-StructureV3 版面解析客户端。

    Args:
        config: PaddleOCR 请求配置。
        http_client: 由容器注入的异步 HTTP client，负责连接池生命周期。
        table_renderer: 表格结构转 Markdown 的渲染器。
        html_renderer: HTML 表格片段转 Markdown 的渲染器。
    """

    def __init__(
        self,
        config: PaddleCloudPPStructureV3Config,
        *,
        http_client: httpx.AsyncClient,
        table_renderer: TableMarkdownRenderer | None = None,
        html_renderer: FragmentMarkdownRenderer | None = None,
    ) -> None:
        if not config.api_url:
            raise ValueError("PaddleOCR API URL is required.")
        if not config.token:
            raise ValueError("PaddleOCR token is required.")
        self._config = config
        self._http = http_client
        self._table_renderer = table_renderer or TableMarkdownRenderer()
        self._html_renderer = html_renderer or FragmentMarkdownRenderer()

    async def parse_page(self, *, file_path: str | Path, page_number: int) -> OcrPageResult:
        path = Path(file_path)
        # PaddleOCR 接收图片输入；PDF 页需要先渲染为 PNG。
        image_bytes = (
            _render_pdf_page_to_png(path, page_number=page_number)
            if path.suffix.lower() == ".pdf"
            else path.read_bytes()
        )
        return await self._parse_bytes(image_bytes, page_number=page_number)

    async def parse_image(self, *, file_path: str | Path) -> OcrPageResult:
        return await self._parse_bytes(Path(file_path).read_bytes(), page_number=1)

    # ------------------------------------------------------------------

    async def _parse_bytes(self, image_bytes: bytes, *, page_number: int) -> OcrPageResult:
        response = await self._request({
            "file": base64.b64encode(image_bytes).decode("ascii"),
            "fileType": 1,
            **dict(self._config.optional_payload),
        })
        return self._to_page_result(response, page_number=page_number)

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"token {self._config.token}",
            "Content-Type": "application/json",
        }
        last_exc: BaseException | None = None
        attempts = max(1, self._config.retries + 1)
        for attempt in range(attempts):
            try:
                resp = await self._http.post(
                    self._config.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self._config.timeout_seconds,
                )
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("PaddleOCR response is not a JSON object.")
                if data.get("errorCode") != 0:
                    raise ValueError(
                        f"PaddleOCR API error {data.get('errorCode')}: {data.get('errorMsg')}"
                    )
                return data
            except Exception as e:
                last_exc = e
                if attempt + 1 < attempts:
                    # 简单线性退避，避免瞬时网络抖动直接失败。
                    await asyncio.sleep(0.2 * (attempt + 1))

        raise PrimaryParserError(
            "PaddleOCR PP-StructureV3 request failed.",
            parser_name=DocumentParseMonitorName.OCR_PADDLE,
            cause=last_exc,
        )

    def _to_page_result(self, response: dict[str, Any], *, page_number: int) -> OcrPageResult:
        parsing_results = response.get("result", {}).get("layoutParsingResults", [])
        page_data: dict[str, Any] = parsing_results[0] if parsing_results else {}

        # useRegionDetection=True 时 API 直接返回整页 markdown，优先使用。
        if top_md := page_data.get("markdown", "").strip():
            return OcrPageResult(page_number=page_number, markdown=top_md)

        # 退化路径：逐元素拼接，兼容 API 未返回整页 Markdown 的情况。
        parts: list[str] = []
        for element in page_data.get("layoutElements", []):
            label = element.get("label", "").lower()
            if "table" in label:
                if md := self._render_table_element(element):
                    parts.append(md)
            elif text := element.get("text", "").strip():
                parts.append(text)

        return OcrPageResult(
            page_number=page_number,
            markdown="\n\n".join(parts),
        )

    def _render_table_element(self, element: dict[str, Any]) -> str | None:
        table: dict[str, Any] = element.get("table") or {}
        if html := table.get("html"):
            return self._html_renderer.render(html)
        if md := table.get("markdown"):
            return md.strip()
        # API 未返回结构化表格数据时退化为纯文本
        return element.get("text", "").strip() or None


def _render_pdf_page_to_png(path: Path, *, page_number: int) -> bytes:
    with fitz.open(str(path)) as doc:
        page_index = page_number - 1
        if not (0 <= page_index < doc.page_count):
            raise ValueError(f"PDF page {page_number} is out of range.")
        return doc.load_page(page_index).get_pixmap(dpi=200, alpha=False).tobytes("png")
