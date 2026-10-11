"""OpenAI 兼容接口的异步文本与结构化生成客户端。"""

import json
from collections.abc import Iterable
from typing import Any, TypeVar

import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class InstructionClient:
    """绑定固定模型，封装文本、JSON 和 Pydantic 结构化生成。"""

    def __init__(self, client: AsyncOpenAI, *, model: str) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        self._client = client
        self._model = model
        self._structured_client = instructor.from_openai(
            client,
            mode=instructor.Mode.JSON,
        )

    async def complete(
        self,
        *,
        messages: Iterable[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> str:
        """生成普通文本，返回非空字符串。

        示例：
            instruction = InstructionClient(openai_client, model="qwen-plus")
            text = await instruction.complete(
                messages=[
                    {"role": "user", "content": "解释 RAG 的原理"},
                ],
            )
        """
        request: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        response = await self._client.chat.completions.create(**request)
        content = response.choices[0].message.content

        if not content or not content.strip():
            raise ValueError("instruction response is empty")

        return content

    async def structured_complete(
        self,
        *,
        messages: Iterable[dict[str, Any]],
        response_model: type[T] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | T:
        """生成结构化数据，默认 JSON，可指定 Pydantic 模型。

        不传 response_model 时，使用 JSON Mode 返回字典。
        传入 response_model 时，由 Instructor 生成并校验。

        示例一：普通 JSON
            result = await instruction.structured_complete(
                messages=[
                    {
                        "role": "user",
                        "content": "以 JSON 格式提取：张三今年 20 岁",
                    },
                ],
            )
            print(result)  # {"name": "张三", "age": 20}

        示例二：Pydantic 结构化输出
            class Person(BaseModel):
                name: str
                age: int

            result = await instruction.structured_complete(
                messages=[
                    {
                        "role": "user",
                        "content": "提取：张三今年 20 岁",
                    },
                ],
                response_model=Person,
            )
            print(result.name, result.age)
        """
        request: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        if response_model is not None:
            return await self._structured_client.chat.completions.create(
                **request,
                response_model=response_model,
            )

        # 默认使用 JSON Mode，并明确要求输出 JSON。
        request["messages"].insert(
            0,
            {"role": "system", "content": "Return a valid JSON object."},
        )
        response = await self._client.chat.completions.create(
            **request,
            response_format={"type": "json_object"},
        )

        if response.choices[0].finish_reason == "length":
            raise ValueError("JSON response was truncated")

        content = response.choices[0].message.content
        if not content:
            raise ValueError("JSON response is empty")

        result = json.loads(content)
        if not isinstance(result, dict):
            # 供应商 JSON 格式不符合合同，沿用响应校验的 ValueError 分类。
            raise ValueError("JSON response must be an object")  # noqa: TRY004

        return result
