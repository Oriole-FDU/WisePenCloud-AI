from enum import StrEnum


class ContentKind(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"
    HTML = "html"
    JSON = "json"
    XML = "xml"
    TEXT = "text"
    UNSUPPORTED_ARCHIVE = "unsupported_archive"
    UNSUPPORTED_MEDIA = "unsupported_media"
    UNSUPPORTED_BINARY = "unsupported_binary"


class DetectionConfidence(StrEnum):
    CONTAINER = "container"
    AI = "ai"
    UNKNOWN = "unknown"


class ContentDetectionDetector(StrEnum):
    FALLBACK_UNKNOWN = "fallback_unknown"
    MAGIKA = "magika"
    ZIP_CLASSIFIER = "zip_classifier"


class ZipValidationError(StrEnum):
    """ZIP 归档文件解析与安全校验的错误原因枚举"""
    TOO_MANY_ENTRIES = "zip_too_many_entries"
    EMPTY_MEMBER_NAME = "zip_empty_member_name"
    UNSAFE_PATH = "zip_unsafe_path"
    NESTED_ARCHIVE = "zip_nested_archive"
    ENTRY_TOO_LARGE = "zip_entry_too_large"
    TOTAL_UNCOMPRESSED_TOO_LARGE = "zip_total_uncompressed_too_large"
    COMPRESSION_RATIO_TOO_HIGH = "zip_compression_ratio_too_high"
    BAD_ZIP = "bad_zip"
    NOT_SUPPORTED_DOCUMENT = "zip_not_supported_document"
    UNSUPPORTED_ODT = "unsupported_odt"
