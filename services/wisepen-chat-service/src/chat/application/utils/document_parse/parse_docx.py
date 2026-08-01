from __future__ import annotations

from pathlib import Path


def parse_docx(file_path: str | Path) -> str:
    file_path = Path(file_path)
    raise NotImplementedError("DOCX parsing is not implemented.")
