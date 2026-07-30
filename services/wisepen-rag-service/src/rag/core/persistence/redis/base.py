from redis.asyncio import Redis


class RedisRepository:
    __slots__ = ("_redis",)

    def __init__(self, *, redis_client: Redis) -> None:
        self._redis = redis_client
