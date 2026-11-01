"""标题用例的内存外部边界；不访问真实 Redis、Qdrant 或论文 API。"""

from scholar.domain.models.titles import TitleMatch, merge_title_identities, normalize_title


class MemoryTitleCache:
    def __init__(self):
        self.entries = {}

    async def get_many(self, titles):
        return [TitleMatch(merge_title_identities(self.entries.get(normalize_title(title), []))) for title in titles]

    async def put_many(self, entries):
        for title, aliases in entries:
            self.entries.setdefault(normalize_title(title), []).append(aliases)


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

    def expire(self, key, ttl):
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
