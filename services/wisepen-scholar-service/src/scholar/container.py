"""Scholar 的依赖装配；应用用例只依赖声明的领域端口。"""

import httpx
from dependency_injector import containers, providers
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from common.utils.ai_clients.embedding import EmbeddingClient
from scholar.application.discovery import PaperDiscoveryService
from scholar.application.lookup import PaperLookupService
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.match import PaperMatchService
from scholar.application.pdf_recover import PaperPdfRecoverService
from scholar.application.search import PaperSearchService, SearchBinding
from scholar.core.config.bootstrap_settings import bootstrap_settings
from scholar.core.config.policy_settings import ScholarPolicyStore
from scholar.core.providers import (
    ArxivProvider,
    HuggingFaceProvider,
    OpenAlexProvider,
    PROVIDER_POLICIES,
    S2Provider,
    S2Transport,
    UnpaywallProvider,
)
from scholar.core.providers.crossref import CrossrefTitleResolver
from scholar.core.storage.qdrant.paper_repository import QdrantPaperRepository
from scholar.core.storage.redis.discovery_cache import RedisDiscoveryCacheRepository
from scholar.core.storage.redis.paper_cache import RedisPaperCache
from scholar.core.storage.redis.query_cache import RedisQueryCacheRepository
from scholar.ml.specter import DIMENSIONS, QUERY_MODEL


class Container(containers.DeclarativeContainer):
    # --- 配置与策略 -------------------------------------------------------
    config = providers.Configuration()
    policy_store = providers.Dependency(instance_of=ScholarPolicyStore)

    # --- 外部连接 ---------------------------------------------------------
    # 连接池与 HTTP 客户端全部 Singleton；超时统一取请求预算。
    qdrant = providers.Singleton(
        AsyncQdrantClient,
        url=config.QDRANT_URL,
        api_key=config.QDRANT_API_KEY,
        timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS,
        trust_env=False,
        cloud_inference=True,
    )
    redis = providers.Singleton(
        Redis.from_url,
        url=config.REDIS_URL,
        socket_timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS,
    )
    openalex_http = providers.Singleton(
        httpx.AsyncClient, timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS
    )
    huggingface_http = providers.Singleton(
        httpx.AsyncClient, timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS
    )
    s2_http = providers.Singleton(
        httpx.AsyncClient, timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS
    )
    crossref_http = providers.Singleton(
        httpx.AsyncClient, timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS
    )
    # Unpaywall 响应轻量，独立使用更短的 5s 超时。
    unpaywall_http = providers.Singleton(httpx.AsyncClient, timeout=5)

    # --- Embedding 服务 ---------------------------------------------------
    # 本地编码服务与主进程同机，绕过环境代理以避免 loopback 请求被转发；固定不重试。
    embedding_api = providers.Singleton(
        AsyncOpenAI,
        api_key="local",
        base_url=f"http://127.0.0.1:{bootstrap_settings.SERVICE_PORT}/v1",
        timeout=config.SCHOLAR_EMBEDDING_TIMEOUT_SECONDS,
        http_client=providers.Factory(
            httpx.AsyncClient,
            timeout=config.SCHOLAR_EMBEDDING_TIMEOUT_SECONDS,
            trust_env=False,
        ),
        max_retries=0,
        default_headers=providers.Callable(
            lambda secret: {"X-From-Source": secret}, config.FROM_SOURCE_SECRET
        ),
    )
    embedding_client = providers.Singleton(
        EmbeddingClient,
        client=embedding_api,
        model=QUERY_MODEL,
        dimensions=DIMENSIONS,
    )

    # --- 缓存层 -----------------------------------------------------------
    paper_cache = providers.Singleton(
        RedisPaperCache,
        redis=redis,
        namespace=config.SCHOLAR_COLLECTION_ALIAS,
        ttl=config.SCHOLAR_PAPER_CACHE_TTL_SECONDS,
    )
    query_cache_repository = providers.Singleton(
        RedisQueryCacheRepository,
        redis=redis,
        namespace=config.SCHOLAR_COLLECTION_ALIAS,
        ttl=config.SCHOLAR_QUERY_CACHE_TTL_SECONDS,
        paper_cache=paper_cache,
    )
    discovery_cache_repository = providers.Singleton(
        RedisDiscoveryCacheRepository,
        redis=redis,
        namespace=config.SCHOLAR_COLLECTION_ALIAS,
        ttl=config.SCHOLAR_DISCOVERY_CACHE_TTL_SECONDS,
    )
    # --- 仓储与本地适配 ---------------------------------------------------
    paper_repository = providers.Singleton(
        QdrantPaperRepository,
        client=qdrant,
        collection=config.SCHOLAR_COLLECTION_ALIAS,
    )
    crossref_resolver = providers.Singleton(
        CrossrefTitleResolver,
        http=crossref_http,
        email=config.UNPAYWALL_EMAIL,
    )
    pdf_service = providers.Singleton(
        PaperPdfRecoverService,
        provider=providers.Singleton(
            UnpaywallProvider,
            http=unpaywall_http,
            email=config.UNPAYWALL_EMAIL,
        ),
        cache=paper_cache,
        ttl_seconds=config.SCHOLAR_PAPER_CACHE_TTL_SECONDS,
    )

    # --- 平台 Provider ----------------------------------------------------
    # 无状态适配器允许启动预热替换编码客户端，仓储和连接仍复用单例。
    arxiv_searcher = providers.Factory(
        ArxivProvider,
        paper_repository=paper_repository,
        embedding_client=embedding_client,
        candidate_limit=config.SCHOLAR_SEARCH_CANDIDATES,
        num_candidates=config.SCHOLAR_SEARCH_NUM_CANDIDATES,
        rrf_k=config.SCHOLAR_SEARCH_RRF_K,
        citation_weight=config.SCHOLAR_SEARCH_CITATION_WEIGHT,
        influential_weight=config.SCHOLAR_SEARCH_INFLUENTIAL_WEIGHT,
    )
    openalex_searcher = providers.Singleton(OpenAlexProvider, http=openalex_http)
    huggingface_provider = providers.Singleton(
        HuggingFaceProvider,
        http=huggingface_http,
        token=config.HF_TOKEN,
        concurrency=config.SCHOLAR_HF_CONCURRENCY,
    )
    # S2 双通道配额协调器：transport 复用单例，provider 只做字段映射。
    s2_transport = providers.Singleton(
        S2Transport,
        http=s2_http,
        redis=redis,
        api_key=config.S2_API_KEY,
        max_queued_ahead=config.SCHOLAR_S2_MAX_QUEUED_AHEAD,
        nokey_interval=config.SCHOLAR_S2_NOKEY_INTERVAL_SECONDS,
        key_interval=config.SCHOLAR_S2_KEY_INTERVAL_SECONDS,
        batch_max_ids=config.SCHOLAR_S2_BATCH_MAX_IDS,
        request_timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS,
    )
    s2_provider = providers.Singleton(S2Provider, transport=s2_transport)

    # --- 富化编排 ---------------------------------------------------------
    # 平台凭证按来源聚合；空值不进入字典，缺 key 的来源自动降级。
    platform_credentials = providers.Callable(
        lambda oa, hf, s2: {
            key: value
            for key, value in {"openalex": oa, "huggingface": hf, "s2": s2}.items()
            if value
        },
        config.OPENALEX_API_KEY,
        config.HF_TOKEN,
        config.S2_API_KEY,
    )
    search_bindings = providers.Dict(
        arxiv=providers.Factory(
            SearchBinding,
            searcher=arxiv_searcher,
            title_matcher=arxiv_searcher.provided.match_titles,
            cache_revision=arxiv_searcher.provided.cache_revision,
            candidate_limit=config.SCHOLAR_SEARCH_CANDIDATES,
        ),
        openalex=providers.Factory(
            SearchBinding,
            searcher=openalex_searcher,
            candidate_limit=200,
            requires_byok=True,
        ),
        huggingface=providers.Factory(
            SearchBinding,
            searcher=huggingface_provider,
            requires_byok=True,
        ),
    )
    lookup_bindings = providers.Dict(
        arxiv=providers.Factory(
            LookupBinding,
            provider=arxiv_searcher,
            cache=paper_cache,
            rules=PROVIDER_POLICIES["arxiv"],
        ),
        s2=providers.Factory(
            LookupBinding,
            provider=s2_provider,
            cache=paper_cache,
            rules=PROVIDER_POLICIES["s2"],
        ),
        openalex=providers.Factory(
            LookupBinding,
            provider=openalex_searcher,
            cache=paper_cache,
            rules=PROVIDER_POLICIES["openalex"],
        ),
        huggingface=providers.Factory(
            LookupBinding,
            provider=huggingface_provider,
            cache=paper_cache,
            rules=PROVIDER_POLICIES["huggingface"],
        ),
    )
    lookup_runner = providers.Singleton(
        LookupRunner,
        bindings=lookup_bindings,
        ttl_seconds=config.SCHOLAR_PAPER_CACHE_TTL_SECONDS,
    )

    # --- 应用用例 ---------------------------------------------------------
    paper_search_service = providers.Singleton(
        PaperSearchService,
        searchers=search_bindings,
        policies=policy_store,
        platform_credentials=platform_credentials,
        query_cache=query_cache_repository,
        lookup=lookup_runner,
        pdf=pdf_service,
    )
    paper_lookup_service = providers.Singleton(
        PaperLookupService,
        lookup=lookup_runner,
        pdf=pdf_service,
        policies=policy_store,
        platform_credentials=platform_credentials,
    )
    paper_match_service = providers.Singleton(
        PaperMatchService,
        cache=paper_cache,
        ttl=config.SCHOLAR_PAPER_CACHE_TTL_SECONDS,
        local=paper_repository,
        crossref=crossref_resolver,
        lookup=paper_lookup_service,
    )
    discovery_bindings = providers.Dict(huggingface=huggingface_provider)
    paper_discovery_service = providers.Singleton(
        PaperDiscoveryService,
        discoverers=discovery_bindings,
        policies=policy_store,
        cache=discovery_cache_repository,
        pdf=pdf_service,
    )


container = Container()
