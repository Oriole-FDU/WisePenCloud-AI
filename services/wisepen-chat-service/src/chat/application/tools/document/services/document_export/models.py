from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from chat.application.tools.document.services.document_export.enums import ExportFormat


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """
    导出渲染选项。

    - 不同 renderer 按需读取其中字段。
    - 未使用的字段由具体 renderer 忽略。
    """

    timeout_seconds: float = 60.0
    assets_dir: Optional[Path] = None
    title: Optional[str] = None
    css: Optional[str] = None
    reference_docx: Optional[Path] = None


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """
    renderer 层统一输入。

    - DocumentExportService 负责构造。
    - 具体 renderer 只根据 request 渲染目标格式文件。
    - output_path 通常是 AtomicExportWriter 提供的临时文件路径。
    """

    session_id: str
    user_id: str
    markdown: str
    target_format: ExportFormat
    output_path: Path
    file_name: Optional[str]
    options: ExportOptions


@dataclass(frozen=True, slots=True)
class GeneratedDocumentFile:
    """
    导出完成后的文件元数据。

    - file_path: 服务端真实文件路径。
    - file_name: 展示给用户的下载文件名。
    - storage_file_name: 服务端内部存储文件名。
    - size_bytes: 最终导出文件大小。
    """

    file_path: Path
    file_name: str
    storage_file_name: str
    user_id: str
    session_id: str
    content_type: str
    target_format: ExportFormat
    size_bytes: int
