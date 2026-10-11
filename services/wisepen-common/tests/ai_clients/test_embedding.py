import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.ai_clients import EmbeddingClient


@pytest.mark.parametrize(
    "options",
    [
        {"model": ""},
        {"model": " \t"},
        {"model": "m", "dimensions": 0},
        {"model": "m", "dimensions": -1},
    ],
)
def test_invalid_initialization_fails_before_io(options):
    create = AsyncMock()
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    with pytest.raises(ValueError):
        EmbeddingClient(client, **options)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_calls_keep_bound_model_and_dimensions():
    async def create(**request):
        await asyncio.sleep(0)
        value = float(request["input"][0])
        return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[value, 0.0])])

    request = AsyncMock(side_effect=create)
    client = EmbeddingClient(
        SimpleNamespace(embeddings=SimpleNamespace(create=request)),
        model="fixed-model",
        dimensions=2,
    )
    results = await asyncio.gather(client.embed(texts=["1"]), client.embed(texts=["2"]))
    assert results == [[[1.0, 0.0]], [[2.0, 0.0]]]
    assert client.dimensions == 2
    assert all(
        call.kwargs["model"] == "fixed-model" and call.kwargs["dimensions"] == 2
        for call in request.await_args_list
    )


@pytest.mark.asyncio
async def test_embed_forwards_optional_dimensions_and_preserves_vectors():
    create = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=(3.0, 4.0)),
                SimpleNamespace(index=0, embedding=(1.0, 2.0)),
            ]
        )
    )
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))

    result = await EmbeddingClient(client, model="text-embedding", dimensions=2).embed(
        texts=["a", "b"]
    )

    assert result == [[1.0, 2.0], [3.0, 4.0]]
    create.assert_awaited_once_with(
        model="text-embedding", input=["a", "b"], dimensions=2
    )


@pytest.mark.asyncio
async def test_embed_omits_dimensions_when_unspecified():
    create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(index=0, embedding=(1.0,))])
    )
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))

    result = await EmbeddingClient(client, model="text-embedding").embed(texts=["a"])

    assert result == [[1.0]]
    create.assert_awaited_once_with(model="text-embedding", input=["a"])


@pytest.mark.asyncio
async def test_embed_rejects_empty_input_and_mismatched_response():
    with pytest.raises(ValueError, match="must not be empty"):
        await EmbeddingClient(SimpleNamespace(), model="m").embed(texts=[])

    create = AsyncMock(return_value=SimpleNamespace(data=[]))
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    with pytest.raises(ValueError, match="count"):
        await EmbeddingClient(client, model="m").embed(texts=["a"])
