from urllib.parse import quote

from chat.application.tools.document.services.document_export.models import GeneratedDocumentFile
from chat.application.tools.document.services.document_export.utils.path import sanitize_path_segment

_DOWNLOAD_REF_USAGE_NOTE = (
    "download_ref is for user download, preview, or document_convert input only. "
    "Do not pass download_ref to web_fetch, document_parse, evidence_rank, or "
    "tool_content_read."
)

_DOWNLOAD_PATH = "/api/document-export/download"


def format_generated_document_result(
    *,
    user_id: str,
    session_id: str,
    generated: GeneratedDocumentFile,
) -> str:
    """将文档导出/转换结果格式化为文本输出，包含 download_ref 和 download_url。"""
    safe_user = sanitize_path_segment(user_id, fallback="user")
    safe_session = sanitize_path_segment(session_id, fallback="default")
    safe_name = sanitize_path_segment(generated.storage_file_name, fallback="document")
    download_ref = f"{safe_user}/{safe_session}/{safe_name}"
    encoded_ref = quote(download_ref, safe="")
    download_url = f"{_DOWNLOAD_PATH}?ref={encoded_ref}"
    return (
        "[Generated Document]\n"
        f"- download_ref: {download_ref}\n"
        f"- download_url: {download_url}\n"
        f"- file_name: {generated.file_name}\n"
        f"- target_format: {generated.target_format}\n"
        f"- content_type: {generated.content_type}\n"
        f"- size_bytes: {generated.size_bytes}\n"
        f"{_DOWNLOAD_REF_USAGE_NOTE}"
    )
