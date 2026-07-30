from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def llm_clients(monkeypatch):
    settings = SimpleNamespace(
        LLM_BASE_URL="https://llm.example.test/v1",
        LLM_API_KEY="test-key",
        QUERY_MODEL="query-model",
        EMBEDDING_MODEL="embedding-model",
        EMBEDDING_DIMENSIONS=1024,
    )
    config_module = ModuleType("rag.core.config.app_settings")
    config_module.settings = settings
    monkeypatch.setitem(sys.modules, config_module.__name__, config_module)

    for module_name in (
        "rag.utils.llm_clients",
        "rag.utils.llm_clients.embedding",
        "rag.utils.llm_clients.query",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    return importlib.import_module("rag.utils.llm_clients")


@pytest.mark.asyncio
async def test_query_client_builds_messages_and_parses_response(llm_clients) -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))],
        usage=SimpleNamespace(total_tokens=7),
    )
    create = _AsyncCreate(response)
    client = llm_clients.QueryClient(
        "query-model",
        api_base="https://llm.example.test/v1",
        api_key="test-key",
        thinking="disabled",
    )
    client._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    result = await client.aquery(
        "latest question",
        system_prompt="system rule",
        messages=[{"role": "assistant", "content": "history"}],
        max_tokens=80,
        response_format={"type": "json_object"},
    )

    assert result.content == "answer"
    assert result.usage_tokens == 7
    assert create.kwargs == {
        "model": "query-model",
        "messages": [
            {"role": "system", "content": "system rule"},
            {"role": "assistant", "content": "history"},
            {"role": "user", "content": "latest question"},
        ],
        "max_tokens": 80,
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "disabled"}},
    }


@pytest.mark.asyncio
async def test_embedding_client_uses_configured_model_and_dimensions(
    llm_clients,
) -> None:
    response = SimpleNamespace(
        data=[SimpleNamespace(embedding=(0.1, 0.2))],
        usage=SimpleNamespace(total_tokens=3),
    )
    create = _AsyncCreate(response)
    client = llm_clients.EmbeddingClient(
        "default-model",
        api_base="https://llm.example.test/v1",
        api_key="test-key",
        dimensions=1024,
    )
    client._async_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=create),
    )

    result = await client.aembed(["first", "second"])

    assert result.embeddings == [[0.1, 0.2]]
    assert result.usage_tokens == 3
    assert create.kwargs == {
        "model": "default-model",
        "input": ["first", "second"],
        "dimensions": 1024,
    }


def test_client_builders_use_application_settings(llm_clients) -> None:
    query_client = llm_clients.build_query_client(thinking="disabled")
    embedding_client = llm_clients.build_embedding_client()

    assert query_client.model == "query-model"
    assert query_client.thinking == "disabled"
    assert embedding_client.model == "embedding-model"
    assert embedding_client.dimensions == 1024


class _AsyncCreate:
    def __init__(self, response) -> None:
        self.response = response
        self.kwargs = None

    async def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.response
