"""Redis 论文缓存共享的载荷版本。"""

# 候选和发现缓存保存出口投影前的完整内部事实，旧紧凑载荷自然过期。
CACHE_REVISION = "candidates-v2"

# 完整 lookup 载荷不兼容旧 metadata-only 记录。
LOOKUP_CACHE_REVISION = "lookup-v2"
