from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from ...errors import DocumentTooLargeError, RemoteParserError
from mineru.backend.pipeline.pipeline_middle_json_mkcontent import union_make
from mineru.utils.enum_class import MakeMode

_IMAGE_DIR = "images"


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
