from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DocumentParseRequest:
    file_path: Path
    original_filename: str | None = None

    @property
    def display_name(self) -> str:
        return self.original_filename or self.file_path.name
