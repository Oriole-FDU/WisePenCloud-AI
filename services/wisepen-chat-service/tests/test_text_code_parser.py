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

from chat.core.providers.attachment_parser import TextCodeAttachmentParser  # noqa: E402


class FakeFileStorageClient:
    async def get_download_url(self, object_key, duration_seconds=900):
        return "https://example.com/download"


class TextCodeAttachmentParserTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parser = TextCodeAttachmentParser(FakeFileStorageClient())

    async def test_parse_txt_extracts_plain_text(self):
        with patch.object(
            self.parser._http,
            "get",
            new=AsyncMock(return_value=SimpleNamespace(
                content="第一行\n第二行".encode("utf-8"),
                raise_for_status=lambda: None,
            )),
        ):
            result = await self.parser.parse("obj", "demo.txt", "txt")

        self.assertEqual(result.extracted_text, "第一行\n第二行")

    async def test_parse_code_strips_hidden_control_chars(self):
        payload = "print('ok')\u202e\n# ignore\x00".encode("utf-8")
        with patch.object(
            self.parser._http,
            "get",
            new=AsyncMock(return_value=SimpleNamespace(
                content=payload,
                raise_for_status=lambda: None,
            )),
        ):
            result = await self.parser.parse("obj", "demo.py", "py")

        self.assertEqual(result.extracted_text, "print('ok')\n# ignore")
        self.assertNotIn("\u202e", result.extracted_text)
        self.assertNotIn("\x00", result.extracted_text)

    async def test_parse_falls_back_to_utf8_replace_when_no_preferred_codec_matches(self):
        payload = b"\x80abc"
        with patch.object(
            self.parser._http,
            "get",
            new=AsyncMock(return_value=SimpleNamespace(
                content=payload,
                raise_for_status=lambda: None,
            )),
        ):
            result = await self.parser.parse("obj", "demo.txt", "txt")

        self.assertEqual(result.extracted_text, "\ufffdabc")


if __name__ == "__main__":
    unittest.main()
