from dataclasses import dataclass
from pathlib import Path

from chat.application.tools.document.services.document_export.enums import ExportFormat


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
