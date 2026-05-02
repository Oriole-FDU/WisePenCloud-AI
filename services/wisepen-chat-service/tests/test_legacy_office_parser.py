import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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

from chat.core.providers.attachment_parser import (  # noqa: E402
    CompositeDocumentAttachmentParser,
    LegacyOfficeAttachmentParser,
)


class FakeFileStorageClient:
    async def get_download_url(self, object_key, duration_seconds=900):
        return "https://example.com/download"


class FakeSimpleParser:
    async def parse(self, object_key, filename, extension):
        return SimpleNamespace(
            summary=f"simple:{extension}",
            content_excerpt="simple excerpt",
            extracted_text="simple text",
        )


class FakeLegacyParser:
    async def parse(self, object_key, filename, extension):
        return SimpleNamespace(
            summary=f"legacy:{extension}",
            content_excerpt="legacy excerpt",
            extracted_text="legacy text",
        )


class FakeTextCodeParser:
    async def parse(self, object_key, filename, extension):
        return SimpleNamespace(
            summary=f"text:{extension}",
            content_excerpt="text excerpt",
            extracted_text="text body",
        )

    @staticmethod
    def supports_extension(extension):
        return extension in {"txt", "md", "py"}


class LegacyOfficeAttachmentParserTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parser = LegacyOfficeAttachmentParser(
            FakeFileStorageClient(),
            converter_command="soffice",
            converter_timeout_seconds=30,
        )

    async def test_parse_doc_uses_local_converter_and_extracts_text(self):
        with patch.object(
            self.parser._http,
            "get",
            new=AsyncMock(return_value=SimpleNamespace(
                content=b"fake-doc-content",
                raise_for_status=lambda: None,
            )),
        ), patch.object(
            self.parser,
            "_convert_to_pdf",
            return_value=Path("converted.pdf"),
        ) as convert_mock, patch.object(
            self.parser,
            "_extract_pdf_text",
            return_value="测试 DOC 正文",
        ):
            result = await self.parser.parse(
                object_key="chat-attachments/u1/s1/demo.doc",
                filename="demo.doc",
                extension="doc",
            )

        convert_mock.assert_called_once()
        self.assertEqual(result.summary, "测试 DOC 正文")
        self.assertEqual(result.extracted_text, "测试 DOC 正文")

    async def test_parse_rejects_unsupported_extension(self):
        with self.assertRaisesRegex(ValueError, "暂不支持旧版 Office 自动解析 docx 格式文件"):
            await self.parser.parse(
                object_key="chat-attachments/u1/s1/demo.docx",
                filename="demo.docx",
                extension="docx",
            )


class CompositeDocumentAttachmentParserTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_legacy_extensions_to_legacy_parser(self):
        parser = CompositeDocumentAttachmentParser(
            simple_parser=FakeSimpleParser(),
            legacy_office_parser=FakeLegacyParser(),
            text_code_parser=FakeTextCodeParser(),
        )

        result = await parser.parse("obj", "demo.doc", "doc")

        self.assertEqual(result.summary, "legacy:doc")

    async def test_dispatches_openxml_extensions_to_simple_parser(self):
        parser = CompositeDocumentAttachmentParser(
            simple_parser=FakeSimpleParser(),
            legacy_office_parser=FakeLegacyParser(),
            text_code_parser=FakeTextCodeParser(),
        )

        result = await parser.parse("obj", "demo.docx", "docx")

        self.assertEqual(result.summary, "simple:docx")

    async def test_dispatches_text_extensions_to_text_code_parser(self):
        parser = CompositeDocumentAttachmentParser(
            simple_parser=FakeSimpleParser(),
            legacy_office_parser=FakeLegacyParser(),
            text_code_parser=FakeTextCodeParser(),
        )

        result = await parser.parse("obj", "demo.py", "py")

        self.assertEqual(result.summary, "text:py")


if __name__ == "__main__":
    unittest.main()
