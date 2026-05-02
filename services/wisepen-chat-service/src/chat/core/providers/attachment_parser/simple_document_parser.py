import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

import httpx

from chat.domain.interfaces import AttachmentParser, AttachmentParseResult
from common.clients.file_storage import FileStorageClient


class SimpleDocumentAttachmentParser(AttachmentParser):
    """最小文档解析器"""

    _SUPPORTED_EXTENSIONS = {"docx", "pptx", "xlsx", "pdf"}
    _SUMMARY_LIMIT = 120
    _EXCERPT_LIMIT = 300

    def __init__(self, file_storage_client: FileStorageClient):
        self._file_storage_client = file_storage_client
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def parse(
        self,
        object_key: str,
        filename: str,
        extension: str,
    ) -> AttachmentParseResult:
        download_url = await self._file_storage_client.get_download_url(object_key)
        resp = await self._http.get(download_url)
        resp.raise_for_status()
        content = resp.content

        extractor = {
            "docx": self._parse_docx,
            "pptx": self._parse_pptx,
            "xlsx": self._parse_xlsx,
            "pdf": self._parse_pdf,
        }.get(extension)
        if extractor is None:
            raise ValueError(f"当前暂不支持自动解析 {extension} 格式文件")

        text = self._normalize_text(extractor(content))
        if not text:
            raise ValueError("未解析出可用文本")

        summary = text[:self._SUMMARY_LIMIT]
        excerpt = text[:self._EXCERPT_LIMIT]
        return AttachmentParseResult(
            summary=summary,
            content_excerpt=excerpt,
            extracted_text=text,
        )

    def _parse_docx(self, content: bytes) -> str:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            return self._extract_xml_text(zf.read("word/document.xml"))

    def _parse_pptx(self, content: bytes) -> str:
        texts: list[str] = []
        with zipfile.ZipFile(BytesIO(content)) as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                    texts.append(self._extract_xml_text(zf.read(name)))
        return "\n".join(filter(None, texts))

    def _parse_xlsx(self, content: bytes) -> str:
        texts: list[str] = []
        with zipfile.ZipFile(BytesIO(content)) as zf:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                shared_strings = [
                    value.text.strip()
                    for value in root.iter()
                    if value.tag.endswith("}t") and value.text and value.text.strip()
                ]

            for name in sorted(zf.namelist()):
                if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
                    continue
                root = ET.fromstring(zf.read(name))
                for cell in root.iter():
                    if not cell.tag.endswith("}c"):
                        continue
                    cell_type = cell.attrib.get("t")
                    value_node = next((child for child in cell if child.tag.endswith("}v")), None)
                    if value_node is None or value_node.text is None:
                        continue
                    raw_value = value_node.text.strip()
                    if not raw_value:
                        continue
                    if cell_type == "s":
                        try:
                            texts.append(shared_strings[int(raw_value)])
                        except Exception:
                            texts.append(raw_value)
                    else:
                        texts.append(raw_value)
        return "\n".join(texts)

    def _parse_pdf(self, content: bytes) -> str:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise ValueError("PDF 自动解析依赖 pypdf，当前环境未安装") from exc

        reader = PdfReader(BytesIO(content))
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        return "\n".join(texts)

    @staticmethod
    def _extract_xml_text(content: bytes) -> str:
        root = ET.fromstring(content)
        texts = []
        for node in root.iter():
            if not node.tag.endswith("}t"):
                continue
            if node.text and node.text.strip():
                texts.append(node.text.strip())
        return "\n".join(texts)

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\x00", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()
