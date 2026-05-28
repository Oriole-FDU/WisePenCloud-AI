import asyncio
import json
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer

from common.logger import log_event, log_error, logger
from common.core.exceptions import ServiceException
from chat.domain.repositories import SessionRepository


class FileUploadedConsumer:
    """订阅 wisepen-storage-file-uploaded-topic，消费 Java 发布的 FileUploadedMessage。

    objectKey 结构: {scenePrefix}/{user_id}/{session_id}/{yyyy/MM/dd}/{uuid}.{ext}
    按 / 分割取 seg[1]=user_id、seg[2]=session_id，更新 ChatSession.attachments 中
    对应条目的 uploaded_at。
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        session_repo: SessionRepository,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._session_repo = session_repo
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            value_deserializer=lambda x: json.loads(x.decode("utf-8")),
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._poll())
        log_event("FileUploadedConsumer 已启动", topic=self._topic, groupId=self._group_id)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        log_event("FileUploadedConsumer 已停止")

    async def _poll(self) -> None:
        try:
            async for msg in self._consumer:
                try:
                    await self._handle(msg.value)
                except Exception as e:
                    log_error("fileUploadedMessage consume", e)
        except asyncio.CancelledError:
            pass

    async def _handle(self, value: dict) -> None:
        object_key = value["objectKey"]
        logger.info("fileUploadedMessage received objectKey={}", object_key)

        parts = object_key.split("/")
        session_id = parts[2]

        try:
            session = await self._session_repo.get_session(session_id)
        except ServiceException:
            return

        for a in session.attachments:
            if a.object_key == object_key and a.uploaded_at is None:
                a.uploaded_at = datetime.now(timezone.utc)
                session.updated_at = datetime.now(timezone.utc)
                await session.save()
                logger.info("attachment uploaded objectKey={}", object_key)
                return
