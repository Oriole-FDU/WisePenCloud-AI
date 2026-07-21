from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..errors import DocumentParserError
from .utils import decode_text

_JSON_LINES_EXTENSIONS = {".jsonl", ".ndjson"}


class JsonConverter:
    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._convert,
            file_path,
            file_name,
        )

    @staticmethod
    def _convert(
        file_path: Path,
        file_name: str,
    ) -> str:
        text = decode_text(file_path.read_bytes(), file_name=file_name)
        suffix = Path(file_name).suffix.lower() or file_path.suffix.lower()

        if suffix in _JSON_LINES_EXTENSIONS:
            return _parse_json_lines(text, file_name)

        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DocumentParserError(
                f"Invalid JSON in {file_name} at line {exc.lineno}, "
                f"column {exc.colno}: {exc.msg}."
            ) from exc

        return (
            "```json\n"
            f"{json.dumps(value, ensure_ascii=False, indent=2)}\n"
            "```"
        )


def _parse_json_lines(text: str, file_name: str) -> str:
    normalized: list[str] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue

        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DocumentParserError(
                f"Invalid JSONL in {file_name} at line {line_number}, "
                f"column {exc.colno}: {exc.msg}."
            ) from exc

        normalized.append(json.dumps(value, ensure_ascii=False))

    return "\n".join(normalized)
