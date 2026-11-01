"""Redis 论文缓存共享的载荷版本。"""

# 候选和发现缓存保存出口投影前的完整内部事实，旧紧凑载荷自然过期。
CACHE_REVISION = "candidates-v2"

# 论文事实、标题索引与 PDF 使用新版本，旧命名空间自然过期。
PAPER_CACHE_REVISION = "paper-v3"
