"""OpenAI 与 TypeSafe JEV 异步决策客户端。"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    JSONContent,
    Noul,
    Question as JevQuestion,
    SystemOneResponse,
)


class DecisionClient:
    """通过 TypeSafe JEV 执行单选、判断和复合决策。

    示例：
        decision = DecisionClient(client=jev_client)  # 已初始化的 AsyncTypeSafeClient
    """

    def __init__(self, client: AsyncTypeSafeClient) -> None:
        self._client = client

    async def choose(
        self,
        *,
        state: JSONContent,
        instructions: str,
        choices: dict[str, str | None],
    ) -> ChoiceAnswer:
        """从候选项中选出最符合指令的一项。

        Args:
            state: 供模型判断的 JSON 状态。
            instructions: 选择依据。
            choices: 选项名称到描述的映射；描述可以为 None。

        Returns:
            JEV ChoiceAnswer，包含 choice、confidence 和 probabilities。

        示例：
            result = await decision.choose(
                state={"query": "搜索最新的 AI 论文"},
                instructions="选择最合适的工具",
                choices={
                    "web": "搜索互联网",
                    "rag": "检索内部知识库",
                    "direct": "直接回答",
                },
            )
            print(result.choice, result.confidence)
        """
        response = await self._client.system_one(
            state=state,
            questions={
                "decision": Choice(instructions=instructions, criteria=choices),
            },
        )
        return response.choices["decision"]

    async def judge(
        self,
        *,
        state: JSONContent,
        instructions: str,
        threshold: float = 0.5,
    ) -> bool:
        """判断条件是否成立，并以 Noul 概率和阈值返回布尔值。

        Args:
            state: 供模型判断的 JSON 状态。
            instructions: 需要判断的条件。
            threshold: 判定为 True 的最小概率，取值范围 [0, 1]。

        Returns:
            条件成立的概率大于或等于阈值时为 True，否则为 False。

        示例：
            should_search = await decision.judge(
                state={"query": "2026 年最新的 Agent Memory 研究"},
                instructions="回答该问题是否需要联网搜索？",
                threshold=0.8,
            )
            if should_search:
                print("需要联网搜索")
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")

        response = await self._client.system_one(
            state=state,
            questions={"decision": Noul(instructions=instructions)},
        )
        return response.nouls["decision"].noul >= threshold

    async def decide(
        self,
        *,
        state: JSONContent,
        questions: Mapping[str, JevQuestion],
    ) -> SystemOneResponse:
        """一次执行多个具名问题，返回 JEV 原生响应。

        所有问题共享 state；questions 支持 Choice、Noul、Score 等类型。

        示例：
            response = await decision.decide(
                state={"query": "删除知识库中的所有文档"},
                questions={
                    "intent": Choice(
                        instructions="判断操作类型",
                        criteria={"read": "读取", "write": "修改", "delete": "删除"},
                    ),
                    "destructive": Noul(instructions="操作是否具有破坏性？"),
                },
            )
            print(response.choices["intent"].choice)
            print(response.nouls["destructive"].noul)
        """
        return await self._client.system_one(state=state, questions=questions)


