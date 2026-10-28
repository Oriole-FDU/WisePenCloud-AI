"""Redis 论文缓存共享的载荷版本。"""

# 紧凑投影切换后直接冷启动缓存，不读取或兼容旧载荷。
CACHE_REVISION = "compact-v1"

# 完整 lookup 载荷不兼容旧 metadata-only 记录，候选与发现缓存沿用原版本。
LOOKUP_CACHE_REVISION = "lookup-v2"
