"""按明确分区、offset 和条数手动重放 DLQ；不删除记录或提交 DLQ 消费位置。"""

import argparse
import asyncio
from contextlib import AsyncExitStack

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from common.logger import info

from rag.backends.kafka import DeliveryHeader


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-servers", required=True)
    parser.add_argument("--topic", required=True, help="原主 topic，来源为 <topic>.DLT")
    parser.add_argument("--partition", type=int, required=True, help="DLQ 分区")
    parser.add_argument("--offset", type=int, required=True, help="DLQ 起始 offset")
    parser.add_argument("--count", type=int, default=1, help="最多重放条数，默认 1")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    if args.partition < 0 or args.offset < 0 or args.count <= 0:
        parser.error("partition/offset must be non-negative; count must be positive")
    if not 0 < args.timeout_seconds < float("inf"):
        parser.error("timeout must be finite and positive")
    return args


async def replay(args: argparse.Namespace) -> int:
    """只读取启动时已存在的记录；每条发送确认后记录进度，失败直接退出。

    来源 offset 不提交，操作者保存已成功重放的位置。未知发送结果可重复投递，
    由业务幂等承接；原字节、key 和业务 headers 不改写。
    """
    source = TopicPartition(f"{args.topic}.DLT", args.partition)
    async with AsyncExitStack() as resources:
        consumer = AIOKafkaConsumer(
            bootstrap_servers=args.bootstrap_servers,
            enable_auto_commit=False,
            group_id=None,
            auto_offset_reset="none",
        )
        resources.push_async_callback(consumer.stop)
        await consumer.start()
        consumer.assign([source])
        consumer.seek(source, args.offset)
        # 固定读取上界，不把执行期间的新失败消息纳入此次人工操作。
        end = (await consumer.end_offsets([source]))[source]
        producer = AIOKafkaProducer(
            bootstrap_servers=args.bootstrap_servers, acks="all"
        )
        resources.push_async_callback(producer.stop)
        await producer.start()
        sent = 0
        while sent < args.count and await consumer.position(source) < end:
            message = await asyncio.wait_for(
                consumer.getone(), timeout=args.timeout_seconds
            )
            if message.offset >= end:
                break
            headers = dict(message.headers)
            topic = headers[DeliveryHeader.ORIGINAL_TOPIC].decode("utf-8")
            partition = int(headers[DeliveryHeader.ORIGINAL_PARTITION])
            if topic != args.topic or partition < 0:
                raise ValueError(
                    "DLQ original topic/partition does not match replay target"
                )
            await asyncio.wait_for(
                producer.send_and_wait(
                    topic,
                    partition=partition,
                    value=message.value,
                    key=message.key,
                    # rag- 是传输层保留前缀；重放时移除诊断及旧版本重试 headers。
                    headers=[
                        (key, value)
                        for key, value in message.headers
                        if not key.startswith("rag-")
                    ],
                ),
                timeout=args.timeout_seconds,
            )
            sent += 1
            info(
                "dlq_record_replayed",
                dlq_topic=source.topic,
                dlq_partition=source.partition,
                dlq_offset=message.offset,
                target_topic=topic,
                target_partition=partition,
            )
        return sent


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sent = asyncio.run(replay(args))
    info("dlq_replay_completed", sent=sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
