from dataclasses import dataclass
from pathlib import Path

from chat.application.tools.document.services.document_export.errors import ExportOutputError
from chat.application.tools.document.services.document_export.utils.path import (
    display_file_name,
    is_path_within_root,
    sanitize_path_segment,
)


@dataclass(frozen=True, slots=True)
class ResolvedDownloadFile:
    """
    已解析的导出下载文件。

    - file_path: 服务端真实文件路径。
    - file_name: 展示给用户的下载文件名。
    - storage_file_name: 服务端内部存储文件名。
    """

    user_id: str
    session_id: str
    file_path: Path
    file_name: str
    storage_file_name: str


class DocumentDownloadResolver:
    """
    download_ref 解析器。

    - 将 download_ref 解析成服务端真实导出文件路径。
    - 校验 user_id，防止跨用户下载。
    - 校验路径片段，防止路径穿越。
    - 校验最终路径仍在 output_root 内。
    """

    def __init__(self, *, output_root: Path):
        """初始化 DocumentDownloadResolver，设置导出文件根目录。"""
        self.output_root = output_root

    def resolve(self, *, download_ref: str, user_id: str) -> ResolvedDownloadFile:
        """将 download_ref 解析为服务端真实文件路径，校验用户隔离和路径安全。"""
        if not download_ref:
            raise ExportOutputError("Missing download ref.")

        # 下载引用固定格式：
        # 用户标识 / 会话标识 / 存储文件名。
        parts = download_ref.split("/")
        if len(parts) != 3:
            raise ExportOutputError("Invalid download ref format.")

        ref_user_id, session_id, storage_file_name = parts
        if not ref_user_id or not session_id or not storage_file_name:
            raise ExportOutputError("Invalid download ref format.")

        # 对 ref 中的路径片段重新清洗，并要求清洗前后完全一致。
        # 这样可以拒绝 ../、路径分隔符、Windows 盘符、非法字符等输入。
        safe_user = sanitize_path_segment(ref_user_id, fallback="")
        expected_user = sanitize_path_segment(user_id, fallback="")
        safe_session = sanitize_path_segment(session_id, fallback="")
        safe_name = sanitize_path_segment(storage_file_name, fallback="")

        # 引用中的用户标识必须等于当前登录用户。
        # 会话标识和存储文件名也必须是安全路径片段。
        if (
            safe_user != ref_user_id
            or expected_user != ref_user_id
            or safe_session != session_id
            or safe_name != storage_file_name
        ):
            raise ExportOutputError("Invalid download ref path segment.")

        # 导出文件目录结构：
        # 输出根目录 / 用户标识 / 会话标识 / 输出目录 / 存储文件名。
        file_path = (
            self.output_root / safe_user / safe_session / "outputs" / safe_name
        ).resolve(strict=False)

        # resolve 后再次确认没有逃逸 output_root。
        if not is_path_within_root(file_path, self.output_root):
            raise ExportOutputError("Resolved download path escapes output root.")

        return ResolvedDownloadFile(
            user_id=safe_user,
            session_id=safe_session,
            file_path=file_path,
            file_name=display_file_name(storage_file_name=safe_name),
            storage_file_name=safe_name,
        )
