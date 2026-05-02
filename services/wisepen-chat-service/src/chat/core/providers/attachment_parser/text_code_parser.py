import re
import codecs

import httpx

from chat.domain.interfaces import AttachmentParser, AttachmentParseResult
from common.clients.file_storage import FileStorageClient


class TextCodeAttachmentParser(AttachmentParser):
    """纯文本与代码文件解析器"""

    _TEXT_EXTENSIONS = {"txt", "md", "markdown"}
    _CODE_EXTENSIONS = {
        "py", "js", "jsx", "ts", "tsx", "java", "go", "c", "cc", "cpp", "h", "hpp",
        "cs", "php", "rb", "rs", "swift", "kt", "kts", "scala", "sh", "bash", "zsh",
        "ps1", "sql", "json", "yaml", "yml", "xml", "html", "css", "scss", "less", "vue",
    }
    _SUPPORTED_EXTENSIONS = _TEXT_EXTENSIONS | _CODE_EXTENSIONS
    _SUMMARY_LIMIT = 120
    _EXCERPT_LIMIT = 300
    _PREFERRED_ENCODINGS = ("utf-8-sig", "utf-16", "gb18030", "big5", "latin-1")

    def __init__(self, file_storage_client: FileStorageClient):
        self._file_storage_client = file_storage_client
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def parse(
        self,
        object_key: str,
        filename: str,
        extension: str,
    ) -> AttachmentParseResult:
        if extension not in self._SUPPORTED_EXTENSIONS:
            raise ValueError(f"当前暂不支持自动解析 {extension} 格式文件")

        download_url = await self._file_storage_client.get_download_url(object_key)
        resp = await self._http.get(download_url)
        resp.raise_for_status()

        text = self._normalize_text(self._decode_text(resp.content))
        if not text:
            raise ValueError("未解析出可用文本")

        return AttachmentParseResult(
            summary=text[:self._SUMMARY_LIMIT],
            content_excerpt=text[:self._EXCERPT_LIMIT],
            extracted_text=text,
        )

    @classmethod
    def supports_extension(cls, extension: str) -> bool:
        return extension in cls._SUPPORTED_EXTENSIONS

    @staticmethod
    def _decode_text(content: bytes) -> str:
        utf16_candidate = (
            content.startswith(codecs.BOM_UTF16_LE)
            or content.startswith(codecs.BOM_UTF16_BE)
            or (len(content) > 4 and content.count(b"\x00") >= max(2, len(content) // 8))
        )
        for encoding in TextCodeAttachmentParser._PREFERRED_ENCODINGS:
            if encoding == "utf-16" and not utf16_candidate:
                continue
            try:
                decoded = content.decode(encoding)
            except UnicodeDecodeError:
                continue
            if encoding != "utf-16" and "\x00" in decoded:
                continue
            return decoded
        return content.decode("utf-8", errors="replace")

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]", "", text)
        lines = [line.rstrip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.strip()
