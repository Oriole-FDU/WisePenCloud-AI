from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.ai_clients import DecisionClient
from typesafe_sdk import Choice, ChoiceAnswer, Noul, NoulAnswer


@pytest.mark.asyncio
async def test_choose_returns_choice_answer_and_forwards_question():
    answer = ChoiceAnswer(
        type="choice",
        choice="web",
        confidence=0.9,
        probabilities={"web": 0.9, "rag": 0.1},
    )
    system_one = AsyncMock(return_value=SimpleNamespace(choices={"decision": answer}))
    client = SimpleNamespace(system_one=system_one)
    state = {"query": "find current research"}

    result = await DecisionClient(client).choose(
        state=state,
        instructions="选择合适的工具",
        choices={"web": "联网搜索", "rag": "内部检索"},
    )

    assert result == answer
    system_one.assert_awaited_once_with(
        state=state,
        questions={
            "decision": Choice(
                instructions="选择合适的工具",
                criteria={"web": "联网搜索", "rag": "内部检索"},
            )
        },
    )


@pytest.mark.asyncio
async def test_judge_compares_noul_probability_with_threshold():
    system_one = AsyncMock(
        return_value=SimpleNamespace(
            nouls={"decision": NoulAnswer(type="noul", noul=0.82)}
        )
    )
    client = SimpleNamespace(system_one=system_one)
    result = await DecisionClient(client).judge(
        state={"query": "latest news"},
        instructions="是否需要联网",
        threshold=0.8,
    )

    assert result is True
    system_one.assert_awaited_once_with(
        state={"query": "latest news"},
        questions={"decision": Noul(instructions="是否需要联网")},
    )


@pytest.mark.parametrize("threshold", [-0.1, 1.1])
@pytest.mark.asyncio
async def test_judge_rejects_threshold_outside_probability_range(threshold):
    system_one = AsyncMock()
    client = SimpleNamespace(system_one=system_one)

    with pytest.raises(ValueError, match="between 0 and 1"):
        await DecisionClient(client).judge(
            state={}, instructions="condition", threshold=threshold
        )

    system_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_decide_returns_full_system_one_response():
    response = SimpleNamespace(
        choices={"intent": "read"},
        nouls={"destructive": False},
    )
    system_one = AsyncMock(return_value=response)
    client = SimpleNamespace(system_one=system_one)
    state = {"query": "delete old documents"}
    questions = {
        "intent": Choice(
            instructions="操作类型",
            criteria={"delete": "删除", "read": "读取"},
        ),
        "destructive": Noul(instructions="是否有破坏性"),
    }

    result = await DecisionClient(client).decide(state=state, questions=questions)

    assert result.choices == response.choices
    assert result.nouls == response.nouls
    system_one.assert_awaited_once_with(state=state, questions=questions)
