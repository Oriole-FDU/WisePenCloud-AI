from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from ...errors import ImageParseError

_POLL_HTTP_TIMEOUT_SECONDS = 30.0
_RESULT_HTTP_TIMEOUT_SECONDS = 60.0
_SUBMIT_HTTP_TIMEOUT_SECONDS = 300.0
_POLL_INTERVAL_SECONDS = 5.0
_MAX_POLL_ATTEMPTS = 60
_OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}


class ImageConverter:
    """通过 PaddleOCR 云端接口将图片转换为 Markdown。"""

    __slots__ = ("_api_url", "_headers", "_http", "_model")

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        api_url: str,
        token: str,
        model: str,
    ) -> None:
        api_url = api_url.strip()
        token = token.strip()
        if not api_url:
            raise ImageParseError("Image parser API URL must not be empty.")
        if not token:
            raise ImageParseError("PaddleOCR token is required.")

        self._api_url = api_url
        self._http = http_client
        self._model = model
        self._headers = {"Authorization": f"Bearer {token}"}

    async def convert(
        self,
        file_path: str | Path,
    ) -> str:
        """解析本地图片或图片 URL，返回 OCR 生成的 Markdown。"""
        source_url = (
            file_path
            if isinstance(file_path, str)
            and file_path.startswith(("http://", "https://"))
            else None
        )
        local_path = None if source_url else Path(file_path)

        if local_path is not None and not local_path.is_file():
            raise FileNotFoundError(local_path)

        job_id = await self._submit_job(
            source_url=source_url,
            file_path=local_path,
        )
        result_url = await self._poll_job(job_id)
        results = await self._download_results(result_url)
        return self._render_results(results)

    async def _submit_job(
        self,
        *,
        source_url: str | None,
        file_path: Path | None,
    ) -> str:
        if source_url is not None:
            response = await self._request_json(
                "POST",
                self._api_url,
                headers={"Content-Type": "application/json"},
                json={
                    "fileUrl": source_url,
                    "model": self._model,
                    "optionalPayload": _OPTIONAL_PAYLOAD,
                },
                timeout=_SUBMIT_HTTP_TIMEOUT_SECONDS,
            )
        else:
            assert file_path is not None
            mime_type = (
                mimetypes.guess_type(file_path.name)[0]
                or "application/octet-stream"
            )
            with file_path.open("rb") as source:
                response = await self._request_json(
                    "POST",
                    self._api_url,
                    data={
                        "model": self._model,
                        "optionalPayload": json.dumps(_OPTIONAL_PAYLOAD),
                    },
                    files={"file": (file_path.name, source, mime_type)},
                    timeout=_SUBMIT_HTTP_TIMEOUT_SECONDS,
                )

        data = response.get("data")
        job_id = data.get("jobId") if isinstance(data, Mapping) else None
        if not isinstance(job_id, str) or not job_id:
            raise ImageParseError("PaddleOCR response missing jobId.")
        return job_id

    async def _poll_job(self, job_id: str) -> str:
        for _ in range(_MAX_POLL_ATTEMPTS):
            response = await self._request_json(
                "GET",
                f"{self._api_url}/{job_id}",
                timeout=_POLL_HTTP_TIMEOUT_SECONDS,
            )
            data = response.get("data")
            if not isinstance(data, Mapping):
                raise ImageParseError("PaddleOCR polling response missing data.")

            state = str(data.get("state") or "pending").strip().lower()
            if state == "done":
                result_url = data.get("resultUrl")
                json_url = (
                    result_url.get("jsonUrl")
                    if isinstance(result_url, Mapping)
                    else None
                )
                if not isinstance(json_url, str) or not json_url:
                    raise ImageParseError("PaddleOCR response missing jsonUrl.")
                return json_url

            if state == "failed":
                raise ImageParseError(
                    "PaddleOCR job failed: "
                    f"{data.get('errorMsg') or 'unknown error'}."
                )

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        raise ImageParseError("PaddleOCR job polling timed out.")

    async def _download_results(self, json_url: str) -> list[dict[str, Any]]:
        try:
            response = await self._http.get(
                json_url,
                timeout=_RESULT_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ImageParseError("PaddleOCR result download timed out.") from exc
        except httpx.HTTPError as exc:
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            suffix = f" with HTTP {status}" if status is not None else ""
            raise ImageParseError(
                f"PaddleOCR result download failed{suffix}."
            ) from exc

        results: list[dict[str, Any]] = []
        for line_number, line in enumerate(response.text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ImageParseError(
                    f"PaddleOCR returned invalid JSONL at line {line_number}."
                ) from exc
            if not isinstance(item, dict):
                raise ImageParseError(
                    f"PaddleOCR returned a non-object result at line {line_number}."
                )
            results.append(item)

        return results

    def _render_results(
        self,
        results: list[dict[str, Any]],
    ) -> str:
        pages: list[str] = []
        for result in results:
            result_data = result.get("result")
            if not isinstance(result_data, Mapping):
                continue
            layout_results = result_data.get("layoutParsingResults")
            if not isinstance(layout_results, list):
                continue
            for layout_result in layout_results:
                if not isinstance(layout_result, Mapping):
                    continue
                markdown = layout_result.get("markdown")
                if not isinstance(markdown, Mapping):
                    continue
                text = markdown.get("text")
                if not isinstance(text, str):
                    continue
                pages.append(text.strip())

        if not pages:
            raise ImageParseError("PaddleOCR returned no Markdown result.")
        return "\n\n".join(page for page in pages if page)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        request_headers = dict(self._headers)
        if headers:
            request_headers.update(headers)
        try:
            response = await self._http.request(
                method,
                url,
                headers=request_headers,
                timeout=timeout,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ImageParseError(
                f"PaddleOCR {method} request timed out."
            ) from exc
        except httpx.HTTPError as exc:
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            suffix = f" with HTTP {status}" if status is not None else ""
            raise ImageParseError(
                f"PaddleOCR {method} request failed{suffix}."
            ) from exc
        except ValueError as exc:
            raise ImageParseError(
                f"PaddleOCR {method} returned invalid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise ImageParseError(
                f"PaddleOCR {method} returned a non-object response."
            )

        error_code = payload.get("errorCode")
        if error_code not in {None, 0, "0"}:
            raise ImageParseError(
                f"PaddleOCR API error {error_code}: "
                f"{payload.get('errorMsg') or 'unknown error'}."
            )
        return payload


@lru_cache(maxsize=1)
def get_image_converter() -> ImageConverter:
    """从平台配置创建并缓存图片 converter。"""
    from chat.core.config.app_settings import settings

    return ImageConverter(
        http_client=httpx.AsyncClient(),
        api_url=settings.PADDLE_OCR_API_URL,
        token=settings.PADDLE_OCR_TOKEN,
        model=settings.PADDLE_OCR_MODEL,
    )
