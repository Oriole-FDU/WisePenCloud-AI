"""手动重放只操作显式位置，原字节和业务 headers 保留，发送失败暴露。"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rag.core.messaging import DeliveryHeader


@pytest.fixture
def script(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/replay_dlq.py"
    spec = importlib.util.spec_from_file_location("_test_replay_dlq", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def replay_env(script, monkeypatch):
    records = [
        SimpleNamespace(
            offset=offset,
            value=b"original JSON",
            key=b"resource",
            headers=[
                (DeliveryHeader.ORIGINAL_TOPIC, b"main"),
                (DeliveryHeader.ORIGINAL_PARTITION, b"2"),
                (DeliveryHeader.ORIGINAL_OFFSET, b"9"),
                (DeliveryHeader.EXCEPTION_MESSAGE, b"failed"),
                ("business", b"unchanged"),
            ],
        )
        for offset in [7, 9]
    ]
    position = 0

    def seek(partition, offset):
        nonlocal position
        position = offset

    async def getone():
        nonlocal position
        message = records.pop(0)
        position = message.offset + 1
        return message

    consumer = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        assign=Mock(),
        seek=Mock(side_effect=seek),
        end_offsets=AsyncMock(side_effect=lambda parts: {parts[0]: 10}),
        position=AsyncMock(side_effect=lambda part: position),
        getone=AsyncMock(side_effect=getone),
        commit=AsyncMock(),
    )
    producer = SimpleNamespace(
        start=AsyncMock(), stop=AsyncMock(), send_and_wait=AsyncMock()
    )
    monkeypatch.setattr(script, "AIOKafkaConsumer", Mock(return_value=consumer))
    monkeypatch.setattr(script, "AIOKafkaProducer", Mock(return_value=producer))
    args = script.parse_args(
        [
            "--bootstrap-servers",
            "broker:9092",
            "--topic",
            "main",
            "--partition",
            "0",
            "--offset",
            "7",
            "--count",
            "2",
        ]
    )
    return SimpleNamespace(
        consumer=consumer, producer=producer, args=args, records=records
    )


async def test_replay_is_bounded_and_preserves_original_partition_without_committing(
        script, replay_env
):
    env = replay_env
    assert await script.replay(env.args) == 2
    for call in env.producer.send_and_wait.call_args_list:
        assert call.args == ("main",)
        assert call.kwargs == {
            "partition": 2,
            "value": b"original JSON",
            "key": b"resource",
            "headers": [("business", b"unchanged")],
        }
    env.consumer.commit.assert_not_awaited()
    env.consumer.stop.assert_awaited_once()
    env.producer.stop.assert_awaited_once()


async def test_replay_stops_at_initial_end_even_when_count_is_larger(
        script, replay_env
):
    replay_env.args.count = 99
    assert await script.replay(replay_env.args) == 2
    assert replay_env.consumer.getone.await_count == 2


async def test_replay_send_failure_preserves_partial_progress_and_closes_connections(
        script, replay_env
):
    failure = OSError("broker down")
    replay_env.producer.send_and_wait.side_effect = [None, failure]
    with pytest.raises(OSError) as caught:
        await script.replay(replay_env.args)
    assert caught.value is failure
    replay_env.consumer.commit.assert_not_awaited()
    replay_env.consumer.stop.assert_awaited_once()
    replay_env.producer.stop.assert_awaited_once()


async def test_wrong_original_topic_does_not_send_anything(script, replay_env):
    replay_env.records[0].headers[0] = (DeliveryHeader.ORIGINAL_TOPIC, b"other")
    with pytest.raises(ValueError, match="does not match"):
        await script.replay(replay_env.args)
    replay_env.producer.send_and_wait.assert_not_awaited()


@pytest.mark.parametrize(
    "option,value",
    [
        ("--partition", "-1"),
        ("--offset", "-1"),
        ("--count", "0"),
        ("--timeout-seconds", "nan"),
    ],
)
def test_replay_invalid_scope_is_rejected_before_connections(script, option, value):
    with pytest.raises(SystemExit):
        script.parse_args(
            [
                "--bootstrap-servers",
                "broker:9092",
                "--topic",
                "main",
                "--partition",
                "0",
                "--offset",
                "7",
                option,
                value,
            ]
        )
