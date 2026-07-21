from pathlib import Path

import httpx
import pytest

from chat.application.utils.ocr import (
    OcrClient,
    OcrConfig,
    OcrError,
)


@pytest.mark.asyncio
async def test_ocr_client_returns_markdown(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"image")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"data": {"jobId": "job-1"}},
            )

        if request.url.path.endswith("/job-1"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "done",
                        "resultUrl": {
                            "jsonUrl": "https://result.example/job-1.jsonl"
                        },
                    }
                },
            )

        return httpx.Response(
            200,
            text=(
                '{"result":{"layoutParsingResults":['
                '{"markdown":{"text":"# OCR"}}]}}\n'
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = OcrClient(
            OcrConfig(
                token="token",
                poll_interval_seconds=0,
            ),
            http_client=http_client,
        )
        result = await client.parse_image(file_path=image_path)

    assert result == "# OCR"
    assert requests[0].headers["Authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_ocr_client_requires_token() -> None:
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(OcrError, match="token is required"):
            OcrClient(
                OcrConfig(),
                http_client=http_client,
            )
