from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ── Tool Execution Timeouts (工具执行超时控制) ──────────────────────
    DOCUMENT_PARSE_TOOL_TIMEOUT_SECONDS: float = 120.0        # 文档解析工具单次执行超时
    WEB_SEARCH_TOOL_TIMEOUT_SECONDS: float = 120.0            # 网页搜索工具单次执行超时
    WEB_FETCH_TOOL_TIMEOUT_SECONDS: float = 120.0             # 网页抓取工具单次执行超时
    WEB_CRAWL_TOOL_TIMEOUT_SECONDS: float = 180.0             # 网页递归爬取工具单次执行超时
    GITHUB_HYDRATE_TOOL_TIMEOUT_SECONDS: float = 20.0         # GitHub 数据富化工具超时
    PAPER_HYDRATE_TOOL_TIMEOUT_SECONDS: float = 20.0          # 论文数据富化工具超时
    EVIDENCE_RANK_TOOL_TIMEOUT_SECONDS: float = 15.0          # 证据排序工具超时
    CREATE_SKILL_TOOL_TIMEOUT_SECONDS: float = 15.0           # 创建技能工具超时
    LOAD_SKILL_TOOL_TIMEOUT_SECONDS: float = 8.0              # 加载技能工具超时
    LOAD_SKILL_ASSET_TOOL_TIMEOUT_SECONDS: float = 8.0        # 加载技能资产工具超时
    MATH_TOOL_TIMEOUT_SECONDS: float = 20.0                   # 数学计算工具超时

    # ── Document Parse & OCR (文档解析与识别详情) ───────────────────────
    PADDLE_OCR_TIMEOUT_SECONDS: float = 300.0                 # PaddleOCR 任务总超时
    PADDLE_OCR_POLL_INTERVAL_SECONDS: float = 5.0             # PaddleOCR 轮询间隔
    PADDLE_OCR_MAX_POLL_ATTEMPTS: int = 60                    # PaddleOCR 最大轮询次数
    PADDLE_OCR_POLL_HTTP_TIMEOUT: float = 30.0                # PaddleOCR 单次轮询请求 HTTP 超时
    PADDLE_OCR_RESULT_HTTP_TIMEOUT: float = 60.0              # PaddleOCR 结果 JSON 获取 HTTP 超时
    DOCUMENT_PARSE_MAX_FILE_REFS: int = 8                     # 单次文档解析最大文件引用数
    DOCUMENT_PARSE_CONCURRENCY: int = 3                       # 文档解析并发数
    DOCUMENT_PARSE_REFRESH_LOCK_TTL_SECONDS: int = 300        # 文档解析刷新锁 TTL

    # ── Web Search (网页搜索策略) ──────────────────────────────────────
    WEB_SEARCH_TIMEOUT_SECONDS: float = 15.0                  # 网页搜索底层 API 超时
    WEB_SEARCH_MAX_HOPS: int = 3                              # 网页搜索最大跳数 (Hops)
    WEB_SEARCH_DEFAULT_RESULTS: int = 10                      # 网页搜索默认返回结果数
    WEB_SEARCH_MAX_RESULTS: int = 20                          # 网页搜索最大返回结果数
    WEB_SEARCH_MAX_SUFFICIENCY_TEXT_CHARS: int = 12_000       # 搜索充分性校验最大文本字符数
    WEB_SEARCH_MAX_RECOMMENDED_CANDIDATES: int = 5            # 最大推荐候选结果数
    WEB_SEARCH_FALLBACK_CANDIDATES_COUNT: int = 3             # 降级兜底候选结果数

    # ── Web Fetch & Crawl (网页抓取与爬取控制) ─────────────────────────
    WEB_FETCH_TIMEOUT_SECONDS: float = 15.0                   # 网页单页抓取超时
    WEB_FETCH_MAX_RESPONSE_BYTES: int = 52_428_800            # 网页抓取最大响应字节数 (50 MiB)
    WEB_FETCH_MIN_TEXT_LENGTH: int = 200                      # 网页抓取有效文本最小长度
    WEB_FETCH_BATCH_CONCURRENCY: int = 5                      # 网页批量抓取并发数
    
    WEB_CRAWL_DEFAULT_MAX_PAGES: int = 20                     # 网页整站爬取默认最大页数
    WEB_CRAWL_MAX_MAX_PAGES: int = 100                        # 网页整站爬取上限最大页数
    WEB_CRAWL_DEFAULT_MAX_DEPTH: int = 2                      # 网页整站爬取默认最大深度
    WEB_CRAWL_MAX_MAX_DEPTH: int = 5                          # 网页整站爬取上限最大深度

    # ── Web Content Cache TTL (网页内容缓存 RFC 9111 控制) ──────────────
    CACHE_DEFAULT_SOFT_TTL_SECONDS: int = 1800                # 智能缓存默认软过期时间 (新鲜期 30min)
    CACHE_DEFAULT_HARD_TTL_SECONDS: int = 7200                # 智能缓存默认硬过期时间 (2h)
    CACHE_MAX_SOFT_TTL_SECONDS: int = 43200                   # 智能缓存软过期上限时间 (12h)
    CACHE_MAX_HARD_TTL_SECONDS: int = 86400                   # 智能缓存硬过期上限时间 (24h)

    # ── Tool Content Store (工具内容存储控制) ──────────────────────────
    TOOL_CONTENT_DEFAULT_TTL_SECONDS: int = 1800              # 工具内容缓存默认存活时间 (30min)
    TOOL_CONTENT_MAX_CHARS: int = 20_000_000                  # 工具内容允许的最大字符长度 (约 20M)

    # ── Tool Run File Store (工具运行文件存储与 GC) ─────────────────────
    TOOL_RUN_FILE_REF_TTL_SECONDS: int = 21_600               # 运行时生成文件引用的默认 TTL (6h)
    TOOL_RUN_FILE_CLEANUP_GRACE_SECONDS: int = 600            # 后台 GC 清理过期文件的安全宽限期 (10min)
    TOOL_RUN_FILE_MAX_BYTES: int = 52_428_800                 # 运行时单文件允许的最大单资产大小 (50 MiB)

    # ── Session & Content Read (会话与内容读取) ────────────────────────
    TOOL_CONTENT_READ_TIMEOUT_SECONDS: float = 5.0            # 单个内容读取超时
    TOOL_CONTENT_READ_MAX_CONTENT_IDS: int = 8                # 单次读取最大内容 ID 数
    TOOL_CONTENT_READ_MAX_REGEX_PATTERN_CHARS: int = 500      # 内容读取正则模式最大字符数
    TOOL_CONTENT_READ_DEFAULT_MAX_MATCHES: int = 10           # 内容读取正则默认最大匹配数
    TOOL_CONTENT_READ_MAX_WINDOW_CHARS: int = 20_000          # 内容读取最大上下文窗口字符数

    TOOL_CONTENT_BATCH_READ_TIMEOUT_SECONDS: float = 5.0      # 批量内容读取超时
    TOOL_CONTENT_BATCH_READ_MAX_ITEMS: int = 8                # 批量内容读取最大条目数
    TOOL_CONTENT_BATCH_READ_MAX_CHUNK_INDICES: int = 8        # 批量内容读取最大分块索引数

    GET_HISTORICAL_CHAT_MESSAGES_TIMEOUT_SECONDS: float = 5.0 # 获取历史聊天记录超时

    # ── Evidence Rank & Math (证据排序与数学计算) ──────────────────────
    EVIDENCE_RANK_MAX_CONTENT_IDS: int = 8                    # 证据排序最大内容 ID 数
    EVIDENCE_RANK_DEFAULT_MAX_EVIDENCE: int = 8               # 证据排序默认最大证据数

    MATH_TOOL_MAX_EXPRESSION_CHARS: int = 2000                # 数学表达式最大字符数


tool_settings = ToolSettings()