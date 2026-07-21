from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
import httpx

_POLL_HTTP_TIMEOUT_SECONDS = 30.0
_RESULT_HTTP_TIMEOUT_SECONDS = 60.0


class OcrError(Exception):
    """OCR 服务调用失败。"""


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """PaddleOCR 云端 API 请求配置。"""

    api_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"  # 创建和查询任务的基础地址
    token: str = ""  # Bearer 凭证
    model: str = "PaddleOCR-VL-1.6"  # PaddleOCR 服务端模型名
    timeout_seconds: float = 300.0  # 创建任务的请求超时
    poll_interval_seconds: float = 5.0  # 任务未完成时的轮询间隔
    max_poll_attempts: int = 60  # 轮询上限，防止任务无限挂起
    optional_payload: Mapping[str, Any] = field(
        # PaddleOCR 要求 multipart 表单字段为 JSON 字符串，而不是嵌套对象。
        default_factory=lambda: {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
    )


class OcrClient:
    """PaddleOCR 云端版面解析客户端。"""

    __slots__ = ("_config", "_headers", "_http")

    def __init__(
        self,
        config: OcrConfig,
        *,
        http_client: httpx.AsyncClient,
    ) -> None:
        token = config.token.strip()
        if not token:
            raise OcrError("PaddleOCR token is required.")

        self._config = config
        self._http = http_client
        self._headers = {"Authorization": f"Bearer {token}"}

    async def parse_page(
        self,
        *,
        file_path: str | Path,
        page_number: int,
    ) -> str:
        """解析 PDF 指定页，其他文件按单张图片处理。"""
        path = Path(file_path)
        if path.suffix.lower() == ".pdf":
            image_bytes = await asyncio.to_thread(
                _render_pdf_page_to_png,
                path,
                page_number,
            )
        else:
            image_bytes = await asyncio.to_thread(path.read_bytes)

        return await self._parse_bytes(image_bytes)

    async def parse_image(
        self,
        *,
        file_path: str | Path,
    ) -> str:
        """解析单张图片。"""
        image_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
        return await self._parse_bytes(image_bytes)

    async def _parse_bytes(
        self,
        image_bytes: bytes,
    ) -> str:
        job_id = await self._submit_job(image_bytes)
        result_url = await self._poll_job(job_id)
        results = await self._download_results(result_url)

        if not results:
            raise OcrError("PaddleOCR returned no page result.")

        # 单图请求只消费第一份版面结果；缺少可选 Markdown 时返回空文本。
        result = results[0].get("result")
        if not isinstance(result, dict):
            return ""

        layout_results = result.get("layoutParsingResults")
        if not isinstance(layout_results, list) or not layout_results:
            return ""

        first_layout = layout_results[0]
        if not isinstance(first_layout, dict):
            return ""

        markdown = first_layout.get("markdown")
        if not isinstance(markdown, dict):
            return ""

        text = markdown.get("text")
        return text.strip() if isinstance(text, str) else ""

    async def _submit_job(self, image_bytes: bytes) -> str:
        response = await self._request_json(
            "POST",
            self._config.api_url,
            timeout=self._config.timeout_seconds,
            data={
                "model": self._config.model,
                # optionalPayload 在 multipart 协议中必须编码为一个文本字段。
                "optionalPayload": json.dumps(
                    dict(self._config.optional_payload)
                ),
            },
            files={
                "file": ("image.png", image_bytes, "image/png"),
            },
        )
        data = response.get("data")
        job_id = data.get("jobId") if isinstance(data, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise OcrError("PaddleOCR response missing jobId.")

        return job_id

    async def _poll_job(self, job_id: str) -> str:
        for _ in range(self._config.max_poll_attempts):
            response = await self._request_json(
                "GET",
                f"{self._config.api_url}/{job_id}",
                timeout=_POLL_HTTP_TIMEOUT_SECONDS,
            )
            data = response.get("data")
            if not isinstance(data, dict):
                raise OcrError("PaddleOCR polling response missing data.")

            state = str(data.get("state") or "pending").strip().lower()
            if state == "done":
                result_url = data.get("resultUrl")
                json_url = (
                    result_url.get("jsonUrl")
                    if isinstance(result_url, dict)
                    else None
                )
                if not isinstance(json_url, str) or not json_url:
                    raise OcrError("PaddleOCR response missing jsonUrl.")
                return json_url

            if state == "failed":
                raise OcrError(
                    "PaddleOCR job failed: "
                    f"{data.get('errorMsg') or 'unknown error'}."
                )

            await asyncio.sleep(self._config.poll_interval_seconds)

        raise OcrError("PaddleOCR job polling timed out.")

    async def _download_results(
        self,
        json_url: str,
    ) -> list[dict[str, Any]]:
        try:
            response = await self._http.get(
                json_url,
                timeout=_RESULT_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise OcrError("PaddleOCR result download timed out.") from exc
        except httpx.HTTPError as exc:
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            suffix = f" with HTTP {status}" if status is not None else ""
            raise OcrError(
                f"PaddleOCR result download failed{suffix}."
            ) from exc

        results: list[dict[str, Any]] = []
        # 服务端将识别结果作为 JSONL 下载，不能直接使用 response.json()。
        for line_number, line in enumerate(
            response.text.splitlines(),
            start=1,
        ):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OcrError(
                    "PaddleOCR returned invalid JSONL at "
                    f"line {line_number}: {exc.msg}."
                ) from exc

            if not isinstance(item, dict):
                raise OcrError(
                    "PaddleOCR returned a non-object result "
                    f"at line {line_number}."
                )

            results.append(item)

        return results

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                url,
                headers=self._headers,
                timeout=timeout,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise OcrError(
                f"PaddleOCR {method} request timed out."
            ) from exc
        except httpx.HTTPError as exc:
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            suffix = f" with HTTP {status}" if status is not None else ""
            raise OcrError(
                f"PaddleOCR {method} request failed{suffix}."
            ) from exc
        except ValueError as exc:
            raise OcrError(
                f"PaddleOCR {method} returned invalid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise OcrError(
                f"PaddleOCR {method} returned a non-object response."
            )

        error_code = payload.get("errorCode")
        if error_code not in {None, 0, "0"}:
            raise OcrError(
                f"PaddleOCR API error {error_code}: "
                f"{payload.get('errorMsg') or 'unknown error'}."
            )

        return payload


def _render_pdf_page_to_png(path: Path, page_number: int) -> bytes:
    """将 PDF 指定页渲染为单张 PNG。"""
    with fitz.open(path) as document:
        page_index = page_number - 1
        if not 0 <= page_index < document.page_count:
            raise OcrError(f"PDF page {page_number} is out of range.")

        # 云端接口只接收图片；200 DPI 在文字清晰度与上传体积之间取平衡。
        return document.load_page(page_index).get_pixmap(
            dpi=200,
            alpha=False,
        ).tobytes("png")
