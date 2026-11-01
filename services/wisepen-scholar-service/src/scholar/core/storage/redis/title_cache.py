"""按完整标题定位 Hash；不同论文同名时保留多个稳定身份，不扫描 key。"""

import hashlib

from pydantic import TypeAdapter
from redis.asyncio import Redis

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.titles import TitleMatch, merge_title_identities, normalize_title


_IDENTITIES = TypeAdapter(list[PaperIdentifier])


class RedisTitleCache:
    def __init__(self, redis: Redis, ttl: int):
        self.redis, self.ttl = redis, ttl

    @staticmethod
    def _key(title: str) -> str:
        return f"scholar:title:{hashlib.sha256(normalize_title(title).encode()).hexdigest()}"

    async def get_many(self, titles: list[str]) -> list[TitleMatch]:
        async with self.redis.pipeline(transaction=False) as pipeline:
            for title in titles:
                pipeline.hvals(self._key(title))
            values = await pipeline.execute()
        return [TitleMatch(merge_title_identities([
            _IDENTITIES.validate_json(value) for value in bucket
        ])) for bucket in values]

    async def put_many(self, entries: list[tuple[str, list[PaperIdentifier]]]) -> None:
        async with self.redis.pipeline(transaction=True) as pipeline:
            for title, aliases in entries:
                if not aliases:
                    continue
                key = self._key(title)
                # 身份组作为 field；较窄的后续来源记录不能抹掉已确认的 arXiv↔DOI 关联。
                payload = _IDENTITIES.dump_json(aliases)
                identity_group = "|".join(sorted(f"{identity.kind.value}:{identity.value}" for identity in aliases))
                pipeline.hset(key, mapping={identity_group: payload})
                pipeline.expire(key, self.ttl)
            await pipeline.execute()
