import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from common.utils.ai_clients import InstructionClient
from pydantic import BaseModel


@pytest.mark.parametrize("model", ["", " \t"])
def test_empty_model_fails_before_client_wrapping(model):
    with patch("common.utils.ai_clients.instruction.instructor.from_openai") as wrap:
        with pytest.raises(ValueError, match="model"):
            InstructionClient(SimpleNamespace(), model=model)
        wrap.assert_not_called()


def _text_client(content: str, *, finish_reason: str = "stop"):
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content=content),
                )
            ]
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, create


@pytest.mark.asyncio
async def test_complete_returns_text_and_forwards_request():
    client, create = _text_client("answer")
    with patch("common.utils.ai_clients.instruction.instructor.from_openai"):
        result = await InstructionClient(client, model="instruction-model").complete(
            messages=[{"role": "user", "content": "question"}],
            max_tokens=64,
        )

    assert result == "answer"
    create.assert_awaited_once_with(
        model="instruction-model",
        messages=[{"role": "user", "content": "question"}],
        max_tokens=64,
    )


@pytest.mark.asyncio
async def test_complete_rejects_empty_text():
    client, _ = _text_client(" ")
    with (
        patch("common.utils.ai_clients.instruction.instructor.from_openai"),
        pytest.raises(ValueError, match="empty"),
    ):
        await InstructionClient(client, model="m").complete(messages=[])


@pytest.mark.asyncio
async def test_complete_rejects_truncation_and_model_is_readonly():
    api, _ = _text_client("valid looking partial", finish_reason="length")
    with patch("common.utils.ai_clients.instruction.instructor.from_openai"):
        client = InstructionClient(api, model="fixed-model")
    assert client.model == "fixed-model"
    with pytest.raises(AttributeError):
        client.model = "other"
    with pytest.raises(ValueError, match="truncated"):
        await client.complete(messages=[], max_tokens=1)


class Person(BaseModel):
    name: str
    age: int


@pytest.mark.asyncio
async def test_structured_complete_uses_json_mode_and_parses_object():
    client, create = _text_client('{"name": "张三", "age": 20}')
    with patch("common.utils.ai_clients.instruction.instructor.from_openai"):
        result = await InstructionClient(
            client, model="instruction-model"
        ).structured_complete(
            messages=[{"role": "user", "content": "extract person"}],
            max_tokens=64,
        )

    assert result == {"name": "张三", "age": 20}
    request = create.await_args.kwargs
    assert request["model"] == "instruction-model"
    assert request["max_tokens"] == 64
    assert request["response_format"] == {"type": "json_object"}
    assert request["messages"][0] == {
        "role": "system",
        "content": "Return a valid JSON object.",
    }
    assert request["messages"][1]["content"] == "extract person"


@pytest.mark.asyncio
async def test_structured_complete_validates_pydantic_model_through_instructor():
    expected = Person(name="张三", age=20)
    structured_create = AsyncMock(return_value=expected)
    structured_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=structured_create))
    )
    with patch("common.utils.ai_clients.instruction.instructor.from_openai") as wrap:
        wrap.return_value = structured_client
        client = InstructionClient(SimpleNamespace(), model="instruction-model")
        result = await client.structured_complete(
            messages=[{"role": "user", "content": "extract person"}],
            response_model=Person,
        )

    assert result is expected
    structured_create.assert_awaited_once_with(
        model="instruction-model",
        messages=[{"role": "user", "content": "extract person"}],
        response_model=Person,
    )


@pytest.mark.parametrize(
    "content,finish_reason,error",
    [
        ("{", "stop", json.JSONDecodeError),
        ('["not", "an", "object"]', "stop", ValueError),
        ('{"partial": true}', "length", ValueError),
        ("", "stop", ValueError),
    ],
)
@pytest.mark.asyncio
async def test_structured_complete_rejects_invalid_json_response(
    content, finish_reason, error
):
    client, _ = _text_client(content, finish_reason=finish_reason)
    with (
        patch("common.utils.ai_clients.instruction.instructor.from_openai"),
        pytest.raises(error),
    ):
        await InstructionClient(client, model="m").structured_complete(messages=[])
