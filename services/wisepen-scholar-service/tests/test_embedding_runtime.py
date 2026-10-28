"""验证公共 EmbeddingClient 能消费固定查询服务，以及边界拒绝不兼容输入。"""

import httpx
import pytest
from common.utils.ai_clients.embedding import EmbeddingClient
from fastapi import FastAPI
from openai import AsyncOpenAI
from scholar.api.endpoints.embeddings import router
from scholar.core.embedding_runtime import QUERY_MODEL


class Encoder:
    def encode(self, inputs):
        return [[float(i)] * 768 for i, _ in enumerate(inputs)]


@pytest.fixture
def app():
    app = FastAPI()
    app.state.encoder = Encoder()
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_query_service_round_trips_through_public_embedding_client(app):
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http,
        AsyncOpenAI(
            api_key="local", base_url="http://encoder/v1", http_client=http
        ) as api,
    ):
        client = EmbeddingClient(api, model=QUERY_MODEL, dimensions=768)
        assert await client.embed(texts=["first", "second"]) == [
            [0.0] * 768,
            [1.0] * 768,
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "wrong-adapter"},
        {"input": []},
        {"input": [[101, 102]]},
        {"dimensions": 4},
    ],
)
async def test_query_service_rejects_incompatible_requests(overrides, app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://encoder",
    ) as http:
        response = await http.post(
            "/v1/embeddings",
            json={"model": QUERY_MODEL, "input": ["query"], **overrides},
        )
    assert response.status_code == 422
