from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from chat.application.tools.document.services.document_export.enums import ExportFormat


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """
    Export rendering options.

    Renderers may read only the fields they support.
    """

    timeout_seconds: float = 60.0
    assets_dir: Optional[Path] = None
    title: Optional[str] = None
    css: Optional[str] = None
    reference_docx: Optional[Path] = None


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """Renderer-layer input built by DocumentExportService."""

    session_id: str
    user_id: str
    markdown: str
    target_format: ExportFormat
    output_path: Path
    file_name: Optional[str]
    options: ExportOptions
