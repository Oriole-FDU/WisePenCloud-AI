"""标题用例的内存外部边界；不访问真实 Redis、Qdrant 或论文 API。"""

class MemoryRedis:
    def __init__(self):
        self.values, self.hashes, self.ttls = {}, {}, {}

    async def get(self, key):
        return self.values.get(key)

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttls[key] = ex

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.hashes.pop(key, None)
            self.ttls.pop(key, None)

    def pipeline(self, *, transaction):
        return MemoryPipeline(self)


class MemoryPipeline:
    def __init__(self, redis):
        self.redis, self.commands = redis, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def set(self, key, value, *, ex):
        self.commands.append(("set", key, value, ex))

    def hset(self, key, *, mapping):
        self.commands.append(("hset", key, mapping))

    async def expire(self, key, ttl):
        self.commands.append(("expire", key, ttl))

    def hvals(self, key):
        self.commands.append(("hvals", key))

    async def execute(self):
        results = []
        for command in self.commands:
            kind, key, *args = command
            if kind == "set":
                await self.redis.set(key, args[0], ex=args[1])
                results.append(True)
            elif kind == "hset":
                self.redis.hashes.setdefault(key, {}).update(args[0])
                results.append(True)
            elif kind == "expire":
                self.redis.ttls[key] = args[0]
                results.append(True)
            else:
                results.append(list(self.redis.hashes.get(key, {}).values()))
        return results
