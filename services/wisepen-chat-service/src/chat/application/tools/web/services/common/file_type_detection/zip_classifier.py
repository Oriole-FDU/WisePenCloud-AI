import io
import zipfile
from typing import Dict, List, Optional, Set, Tuple

from chat.application.tools.web.services.common.file_type_detection.enums import (
    ContentDetectionDetector,
    ContentKind,
    DetectionConfidence,
    ZipValidationError,
)
from chat.application.tools.web.services.common.file_type_detection.models import ContentDetection

_MAX_ZIP_ENTRY_UNCOMPRESSED = 32 * 1024 * 1024
_NESTED_ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz")

# 支持的开放文档格式映射：原始类型到内容类型、后缀和原因。
_OPENDOCUMENT_SUPPORTED: Dict[bytes, Tuple[str, str, str]] = {
    b"application/epub+zip": (
        "application/epub+zip", ".epub", "epub_mimetype",
    ),
    b"application/vnd.oasis.opendocument.spreadsheet": (
        "application/vnd.oasis.opendocument.spreadsheet", ".ods", "ods_mimetype",
    ),
}

# 已知但目前不支持的 OpenDocument 格式
_OPENDOCUMENT_UNSUPPORTED: Dict[bytes, ZipValidationError] = {
    b"application/vnd.oasis.opendocument.text": ZipValidationError.UNSUPPORTED_ODT,
}

# Office Open XML 内容类型文件关键字特征映射，宏格式优先匹配。
_OOXML_CONTENT_TYPE_SIGNATURES: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "application/vnd.ms-word.document.macroEnabled",
        "application/vnd.ms-word.document.macroenabled.12",
        ".docm",
        "ooxml_docm_content_types",
    ),
    (
        "application/vnd.ms-powerpoint.presentation.macroEnabled",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
        ".pptm",
        "ooxml_pptm_content_types",
    ),
    (
        "application/vnd.ms-excel.sheet.macroEnabled",
        "application/vnd.ms-excel.sheet.macroenabled.12",
        ".xlsm",
        "ooxml_xlsm_content_types",
    ),
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
        "ooxml_docx_content_types",
    ),
    (
        "application/vnd.openxmlformats-officedocument.presentationml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
        "ooxml_pptx_content_types",
    ),
    (
        "application/vnd.openxmlformats-officedocument.spreadsheetml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
        "ooxml_xlsx_content_types",
    ),
)

# Office Open XML 目录路径降级识别特征。
_OOXML_PATH_SIGNATURES: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
        "ooxml_docx_path",
    ),
    (
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
        "ooxml_pptx_path",
    ),
    (
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
        "ooxml_xlsx_path",
    ),
)


def classify_zip_document(content: bytes) -> ContentDetection:
    """解析并分类 ZIP 容器格式文档（如 Office/EPUB/ODS），包含安全校验。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content), mode="r") as zf:
            infos = zf.infolist()
            # 基础元数据安全检查
            safety_error = _validate_zip_document_metadata(infos)
            if safety_error:
                return _build_unsupported_zip_detection(safety_error)

            names = {info.filename for info in infos}
            return _classify_validated_zip_document(zf, names)
    except zipfile.BadZipFile:
        return _build_unsupported_zip_detection(ZipValidationError.BAD_ZIP)


def _build_document_detection(mime_type: str, extension: str, reason: str) -> ContentDetection:
    """构建支持的文档类型检测结果。"""
    return ContentDetection(
        kind=ContentKind.DOCUMENT,
        mime_type=mime_type,
        extension=extension,
        confidence=DetectionConfidence.CONTAINER,
        reason=reason,
        detector=ContentDetectionDetector.ZIP_CLASSIFIER,
    )


def _build_unsupported_zip_detection(reason: ZipValidationError) -> ContentDetection:
    """构建不支持或异常的 ZIP 检测结果。"""
    return ContentDetection(
        kind=ContentKind.UNSUPPORTED_ARCHIVE,
        mime_type="application/zip",
        extension=".zip",
        confidence=DetectionConfidence.CONTAINER,
        reason=reason,
        detector=ContentDetectionDetector.ZIP_CLASSIFIER,
    )


def _classify_validated_zip_document(
        zf: zipfile.ZipFile,
        names: Set[str],
) -> ContentDetection:
    """对通过安全校验的 ZIP 文档进行具体格式细分。"""
    # 优先检测开放文档类型的类型声明文件。
    mimetype_bytes = _read_zip_metadata_file(zf, "mimetype")
    if mimetype_bytes:
        mimetype = mimetype_bytes.strip()

        supported = _OPENDOCUMENT_SUPPORTED.get(mimetype)
        if supported:
            mime_type, extension, reason = supported
            return _build_document_detection(mime_type, extension, reason)

        unsupported_error = _OPENDOCUMENT_UNSUPPORTED.get(mimetype)
        if unsupported_error:
            return _build_unsupported_zip_detection(unsupported_error)

    # 检测 Office Open XML 格式。
    ooxml = _classify_ooxml(zf, names)
    if ooxml:
        return ooxml

    return _build_unsupported_zip_detection(ZipValidationError.NOT_SUPPORTED_DOCUMENT)


def _validate_zip_document_metadata(infos: List[zipfile.ZipInfo]) -> Optional[ZipValidationError]:
    """检查 ZIP 归档元数据，防御 ZIP 炸弹及路径穿越等安全风险。"""
    if len(infos) > 4096:
        return ZipValidationError.TOO_MANY_ENTRIES

    total_uncompressed = 0
    for info in infos:
        name = info.filename.replace("\\", "/")

        if not name:
            return ZipValidationError.EMPTY_MEMBER_NAME
        if name.startswith("/") or name.startswith("../") or "/../" in name:
            return ZipValidationError.UNSAFE_PATH
        if name.lower().endswith(_NESTED_ARCHIVE_SUFFIXES):
            return ZipValidationError.NESTED_ARCHIVE
        if info.file_size > _MAX_ZIP_ENTRY_UNCOMPRESSED:
            return ZipValidationError.ENTRY_TOO_LARGE

        total_uncompressed += info.file_size
        if total_uncompressed > 256 * 1024 * 1024:
            return ZipValidationError.TOTAL_UNCOMPRESSED_TOO_LARGE

        # 检查压缩比
        if info.compress_size > 0 and info.file_size / info.compress_size > 100:
            return ZipValidationError.COMPRESSION_RATIO_TOO_HIGH

    return None


def _read_zip_metadata_file(zf: zipfile.ZipFile, name: str) -> Optional[bytes]:
    """安全读取指定的单个元数据文件，限制最大大小与压缩比。"""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None

    if info.file_size > 1 * 1024 * 1024:
        return None

    if info.compress_size > 0 and info.file_size / info.compress_size > 100:
        return None

    return zf.read(name)


def _classify_ooxml(
        zf: zipfile.ZipFile,
        names: Set[str],
) -> Optional[ContentDetection]:
    """通过 [Content_Types].xml 或目录路径结构识别 OOXML (Office) 格式。"""
    xml_bytes = _read_zip_metadata_file(zf, "[Content_Types].xml")
    if xml_bytes:
        xml = xml_bytes.decode("utf-8", errors="replace")
        for signature, mime_type, extension, reason in _OOXML_CONTENT_TYPE_SIGNATURES:
            if signature in xml:
                return _build_document_detection(mime_type, extension, reason)

    # 降级方案：根据特定关键文件路径判定格式
    lowered_names = {name.lower() for name in names}
    for path, mime_type, extension, reason in _OOXML_PATH_SIGNATURES:
        if path in lowered_names:
            return _build_document_detection(mime_type, extension, reason)

    return None
