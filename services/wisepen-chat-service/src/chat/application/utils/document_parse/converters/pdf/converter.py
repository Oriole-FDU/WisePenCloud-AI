from __future__ import annotations

import asyncio
import json
import tempfile
import zipfile
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path, PurePosixPath

import anyio
import httpx
from mineru.backend.pipeline.pipeline_middle_json_mkcontent import union_make
from mineru.utils.enum_class import MakeMode

from ...errors import (
    DocumentTooLargeError,
    RemoteParserError,
    RemoteParserTimeoutError,
)

_STREAM_CHUNK_SIZE = 1024 * 1024
_IMAGE_DIR = "images"
_PDF_PARSE_FORM: dict[str, str] = {
    "backend": "pipeline",
    "lang_list": "ch",
    "parse_method": "auto",
    "formula_enable": "true",
    "table_enable": "true",
    "return_md": "false",
    "return_content_list": "false",
    "return_middle_json": "true",
    "return_model_output": "false",
    "return_images": "false",
    "response_format_zip": "true",
    "return_original_file": "false",
    "client_side_output_generation": "false",
    "start_page_id": "0",
    "end_page_id": "99999",
}


# --- 远程 PDF 解析请求和 ZIP 响应下载 ---

class PdfConverter:
    """通过远程 PDF 解析服务生成 Markdown。"""

    __slots__ = (
        "_api_url",
        "_connect_timeout_seconds",
        "_http_client",
        "_max_response_bytes",
        "_pool_timeout_seconds",
        "_read_timeout_seconds",
        "_write_timeout_seconds",
    )

    def __init__(
            self,
            *,
            http_client: httpx.AsyncClient,
            api_url: str,
            connect_timeout_seconds: float = 20.0,
            write_timeout_seconds: float = 1800.0,
            read_timeout_seconds: float = 3600.0,
            pool_timeout_seconds: float = 20.0,
            max_response_bytes: int = 104_857_600,
    ) -> None:
        api_url = api_url.strip()
        if not api_url:
            raise ValueError("PDF parser API URL must not be empty.")

        self._http_client = http_client
        self._api_url = api_url
        self._connect_timeout_seconds = max(0.1, float(connect_timeout_seconds))
        self._write_timeout_seconds = max(0.1, float(write_timeout_seconds))
        self._read_timeout_seconds = max(0.1, float(read_timeout_seconds))
        self._pool_timeout_seconds = max(0.1, float(pool_timeout_seconds))
        self._max_response_bytes = max(1, int(max_response_bytes))

    async def convert(
            self,
            file_path: Path,
            *,
            file_name: str,
    ) -> str:
        if not file_path.is_file():
            raise FileNotFoundError(file_path)

        upload_file_name = file_name if file_name.lower().endswith(".pdf") else f"{file_name}.pdf"

        with tempfile.TemporaryDirectory(prefix="pdf_parse_result_") as temp_dir:
            zip_path = Path(temp_dir) / "result.zip"
            await self._request_parse(
                file_path=file_path,
                upload_file_name=upload_file_name,
                mime_type="application/pdf",
                zip_path=zip_path,
            )
            markdown = await asyncio.to_thread(
                extract_pdf_markdown,
                zip_path,
                file_name=file_name,
                max_output_bytes=self._max_response_bytes,
            )

        return markdown

    async def _request_parse(
            self,
            *,
            file_path: Path,
            upload_file_name: str,
            mime_type: str,
            zip_path: Path,
    ) -> None:
        timeout = httpx.Timeout(
            connect=self._connect_timeout_seconds,
            write=self._write_timeout_seconds,
            read=self._read_timeout_seconds,
            pool=self._pool_timeout_seconds,
        )

        try:
            with file_path.open("rb") as source:
                async with self._http_client.stream(
                        "POST",
                        self._api_url,
                        headers={"Accept": "application/zip"},
                        data=_PDF_PARSE_FORM,
                        files={"files": (upload_file_name, source, mime_type)},
                        timeout=timeout,
                ) as response:
                    if response.is_error:
                        raise RemoteParserError(
                            f"PDF parsing failed with HTTP {response.status_code}."
                        )

                    await self._write_zip_response(
                        response,
                        zip_path=zip_path,
                    )
        except (RemoteParserError, DocumentTooLargeError):
            raise
        except httpx.TimeoutException as exc:
            raise RemoteParserTimeoutError("PDF parsing request timed out.") from exc
        except httpx.HTTPError as exc:
            raise RemoteParserError(f"PDF parsing request failed: {exc}.") from exc

    async def _write_zip_response(
            self,
            response: httpx.Response,
            *,
            zip_path: Path,
    ) -> None:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise RemoteParserError(
                    "PDF parser returned an invalid content length."
                ) from exc

            if declared_size < 0:
                raise RemoteParserError(
                    "PDF parser returned a negative content length."
                )

            if declared_size > self._max_response_bytes:
                raise DocumentTooLargeError(
                    f"PDF parser result exceeds {self._max_response_bytes} bytes."
                )

        written = 0
        signature = bytearray()

        async with await anyio.open_file(zip_path, "wb") as output:
            async for chunk in response.aiter_bytes(
                    chunk_size=_STREAM_CHUNK_SIZE,
            ):
                if len(signature) < 2:
                    signature.extend(chunk[: 2 - len(signature)])

                written += len(chunk)
                if written > self._max_response_bytes:
                    raise DocumentTooLargeError(
                        f"PDF parser result exceeds {self._max_response_bytes} bytes."
                    )

                await output.write(chunk)

        content_type = response.headers.get("content-type", "").lower()
        if written == 0 or ("zip" not in content_type and bytes(signature) != b"PK"):
            raise RemoteParserError("PDF parser did not return a ZIP result.")


# --- MinerU 结果 ZIP 读取和 Markdown 渲染 ---

def extract_pdf_markdown(
        zip_path: Path,
        *,
        file_name: str,
        max_output_bytes: int,
) -> str:
    """从 PDF 解析结果 ZIP 中读取 middle JSON 并按页渲染 Markdown。"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [
                info
                for info in archive.infolist()
                if not info.is_dir()
            ]
            middle_json_info = _select_middle_json(
                members,
                file_name=file_name,
            )
            if middle_json_info.file_size > max_output_bytes:
                raise DocumentTooLargeError(
                    f"PDF parser ZIP member {middle_json_info.filename} for "
                    f"{file_name} exceeds {max_output_bytes} bytes."
                )

            try:
                middle_json = json.loads(
                    archive.read(middle_json_info).decode("utf-8")
                )
            except (ValueError, UnicodeDecodeError) as exc:
                raise RemoteParserError(
                    f"PDF parser middle JSON for {file_name} is not valid JSON."
                ) from exc

            markdown = _render_markdown_pages(middle_json)
            if len(markdown.encode("utf-8")) > max_output_bytes:
                raise DocumentTooLargeError(
                    f"Rendered PDF Markdown for {file_name} "
                    f"exceeds {max_output_bytes} bytes."
                )
            return markdown
    except (RemoteParserError, DocumentTooLargeError):
        raise
    except zipfile.BadZipFile as exc:
        raise RemoteParserError(
            f"PDF parser result for {file_name} is not a valid ZIP archive."
        ) from exc
    except Exception as exc:
        raise RemoteParserError(
            f"Failed to read PDF parser result for {file_name}."
        ) from exc


def _select_middle_json(
        members: list[zipfile.ZipInfo],
        *,
        file_name: str,
) -> zipfile.ZipInfo:
    middle_json_files = [
        info
        for info in members
        if PurePosixPath(info.filename).name.lower().endswith("_middle.json")
    ]
    if len(middle_json_files) != 1:
        raise RemoteParserError(
            f"PDF parser result for {file_name} does not contain unique middle JSON."
        )

    return middle_json_files[0]


def _render_markdown_pages(middle_json: object) -> str:
    """按 middle JSON 的页面边界渲染 Markdown，并在每页前写入页标。"""
    if not isinstance(middle_json, Mapping):
        raise ValueError("MinerU middle JSON must be an object.")

    pdf_info = middle_json.get("pdf_info")
    if not isinstance(pdf_info, list):
        raise ValueError("MinerU middle JSON must contain a pdf_info list.")

    pages: list[str] = []
    for index, page_info in enumerate(pdf_info):
        if not isinstance(page_info, Mapping):
            raise ValueError("MinerU pdf_info entries must be objects.")

        page_idx = page_info.get("page_idx", index)
        if type(page_idx) is not int or page_idx < 0:
            raise ValueError("MinerU page_idx must be a non-negative integer.")

        page_markdown = union_make([page_info], MakeMode.MM_MD, _IMAGE_DIR).strip()
        marker = f"<!-- page {page_idx + 1} -->"
        pages.append(f"{marker}\n\n{page_markdown}" if page_markdown else marker)

    return "\n\n".join(pages)


# --- converter 缓存入口 ---

@lru_cache(maxsize=8)
def get_pdf_converter(api_url: str) -> PdfConverter:
    """按解析服务地址复用长期 HTTP 客户端和 PDF converter。"""
    return PdfConverter(
        http_client=httpx.AsyncClient(),
        api_url=api_url,
    )
