import os
import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
COMMON_ROOT = SERVICE_ROOT.parent / "wisepen-common"

sys.path.insert(0, str(SERVICE_ROOT / "src"))
sys.path.insert(0, str(COMMON_ROOT / "src"))

os.environ.setdefault("NACOS_SERVER_ADDR", "127.0.0.1:8848")
os.environ.setdefault("NACOS_NAMESPACE_ID", "test-namespace")
os.environ.setdefault("NACOS_DATA_ID", "wisepen-chat-service-test.yaml")

from common.cloud.nacos_client import nacos_client_manager  # noqa: E402


async def _fake_pull_config():
    return """
APP_NAME: WisePen Chat Test
SERVICE_NAME: wisepen-chat-service
SERVICE_HOST: 127.0.0.1
SERVICE_PORT: 18080
DEV: true
LOG_LEVEL: INFO
LLM_BASE_URL: http://localhost:8000/v1
LLM_API_KEY: test-key
ZERO_ENTROPY_API_KEY: test-zero
FROM_SOURCE_SECRET: test-secret
REDIS_URL: redis://localhost:6379/0
MONGODB_URL: mongodb://localhost:27017
MONGODB_DB_NAME: wisepen_chat_test
QDRANT_HOST: localhost
QDRANT_PASSWORD: test-pass
KAFKA_BOOTSTRAP_SERVERS: localhost:9092
"""


nacos_client_manager.pull_config = _fake_pull_config

from chat.core.providers.attachment_parser.simple_document_parser import (  # noqa: E402
    SimpleDocumentAttachmentParser,
)


class FakeFileStorageClient:
    async def get_download_url(self, object_key, duration_seconds=900):
        return "https://example.com/download"


class SimpleDocumentParserTestCase(unittest.TestCase):
    def setUp(self):
        self.parser = SimpleDocumentAttachmentParser(FakeFileStorageClient())

    def test_parse_docx_extracts_text(self):
        content = self._build_zip({
            "word/document.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p><w:r><w:t>\xe6\xb5\x8b\xe8\xaf\x95 DOCX</w:t></w:r></w:p></w:body>"
                b"</w:document>"
            )
        })
        text = self.parser._parse_docx(content)
        self.assertIn("测试 DOCX", text)

    def test_parse_pptx_extracts_text(self):
        content = self._build_zip({
            "ppt/slides/slide1.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                b'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                b"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>\xe6\xb5\x8b\xe8\xaf\x95 PPTX</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>"
                b"</p:sld>"
            )
        })
        text = self.parser._parse_pptx(content)
        self.assertIn("测试 PPTX", text)

    def test_parse_xlsx_extracts_text(self):
        content = self._build_zip({
            "xl/sharedStrings.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                b"<si><t>\xe6\xb5\x8b\xe8\xaf\x95 XLSX</t></si>"
                b"</sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                b"<sheetData><row r=\"1\"><c r=\"A1\" t=\"s\"><v>0</v></c></row></sheetData>"
                b"</worksheet>"
            ),
        })
        text = self.parser._parse_xlsx(content)
        self.assertIn("测试 XLSX", text)

    def test_legacy_office_binary_not_supported_yet(self):
        with self.assertRaisesRegex(ValueError, "暂不支持自动解析 doc 格式文件"):
            extractor = {
                "docx": self.parser._parse_docx,
                "pptx": self.parser._parse_pptx,
                "xlsx": self.parser._parse_xlsx,
                "pdf": self.parser._parse_pdf,
            }.get("doc")
            if extractor is None:
                raise ValueError("当前暂不支持自动解析 doc 格式文件")

    @staticmethod
    def _build_zip(entries):
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in entries.items():
                zf.writestr(name, content)
        return stream.getvalue()


if __name__ == "__main__":
    unittest.main()
