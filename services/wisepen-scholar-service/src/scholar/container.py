"""Scholar 的依赖装配；应用用例只依赖声明的领域端口。"""

import httpx
from common.utils.ai_clients.embedding import EmbeddingClient
from dependency_injector import containers, providers
from openai import AsyncOpenAI
from redis.asyncio import Redis

from scholar.application.discovery import PaperDiscoveryService
from scholar.application.field_policies import FIELD_POLICIES
from scholar.application.lookup import PaperLookupService
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.search import (
    PaperSearchService,
    SearchBinding,
)
from scholar.core.config.bootstrap_settings import bootstrap_settings
from scholar.core.embedding_runtime import DIMENSIONS, QUERY_MODEL
from scholar.core.storage.elasticsearch.paper_repository import (
    ElasticsearchPaperRepository,
    create_elasticsearch,
)
from scholar.core.storage.redis.discovery_cache import RedisDiscoveryCacheRepository
from scholar.core.storage.redis.paper_cache import RedisPaperLookupCache
from scholar.core.storage.redis.query_cache_repository import RedisQueryCacheRepository
from scholar.domain.models.identifiers import IdentifierKind
from scholar.providers import arxiv, huggingface, openalex, s2
from scholar.providers.arxiv import ArxivProvider
from scholar.providers.huggingface import HuggingFaceProvider
from scholar.providers.openalex import OpenAlexProvider
from scholar.providers.s2 import S2Provider, S2Transport


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()
    elasticsearch = providers.Singleton(
        create_elasticsearch,
        uris=config.ELASTICSEARCH_URIS,
        username=config.ELASTICSEARCH_USERNAME,
        password=config.ELASTICSEARCH_PASSWORD,
        timeout=config.SCHOLAR_REQUEST_TIMEOUT_SECONDS,
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
    s2_transport = providers.Singleton(
        S2Transport,
        http=s2_http,
        redis=redis,
        api_key=config.S2_API_KEY,
        max_queued_ahead=config.SCHOLAR_S2_MAX_QUEUED_AHEAD,
    )
    s2_provider = providers.Singleton(S2Provider, transport=s2_transport)
    paper_repository = providers.Singleton(
        ElasticsearchPaperRepository, es=elasticsearch, alias=config.SCHOLAR_INDEX_ALIAS
    )
    query_cache_repository = providers.Singleton(
        RedisQueryCacheRepository,
        redis=redis,
        namespace=config.SCHOLAR_INDEX_ALIAS,
        ttl=config.SCHOLAR_QUERY_CACHE_TTL_SECONDS,
    )
    discovery_cache_repository = providers.Singleton(
        RedisDiscoveryCacheRepository,
        redis=redis,
        namespace=config.SCHOLAR_INDEX_ALIAS,
        ttl=config.SCHOLAR_DISCOVERY_CACHE_TTL_SECONDS,
    )
    embedding_api = providers.Singleton(
        AsyncOpenAI,
        api_key="local",
        base_url=f"http://127.0.0.1:{bootstrap_settings.SERVICE_PORT}/v1",
        timeout=config.SCHOLAR_EMBEDDING_TIMEOUT_SECONDS,
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
    arxiv_searcher = providers.Singleton(
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
    arxiv_cache = providers.Singleton(
        RedisPaperLookupCache,
        redis=redis,
        namespace=providers.Callable(
            lambda alias: f"{alias}:arxiv", config.SCHOLAR_INDEX_ALIAS
        ),
    )
    s2_cache = providers.Singleton(
        RedisPaperLookupCache,
        redis=redis,
        namespace=providers.Callable(
            lambda alias: f"{alias}:s2", config.SCHOLAR_INDEX_ALIAS
        ),
    )
    openalex_cache = providers.Singleton(
        RedisPaperLookupCache,
        redis=redis,
        namespace=providers.Callable(
            lambda alias: f"{alias}:openalex", config.SCHOLAR_INDEX_ALIAS
        ),
    )
    huggingface_cache = providers.Singleton(
        RedisPaperLookupCache,
        redis=redis,
        namespace=providers.Callable(
            lambda alias: f"{alias}:huggingface", config.SCHOLAR_INDEX_ALIAS
        ),
    )
    platform_credentials = providers.Callable(
        lambda oa, hf: {
            k: v for k, v in {"openalex": oa, "huggingface": hf}.items() if v
        },
        config.OPENALEX_API_KEY,
        config.HF_TOKEN,
    )
    search_bindings = providers.Dict(
        arxiv=providers.Factory(
            SearchBinding,
            searcher=arxiv_searcher,
            cache_revision=arxiv_searcher.provided.cache_revision,
            candidate_limit=config.SCHOLAR_SEARCH_CANDIDATES,
        ),
        openalex=providers.Factory(
            SearchBinding,
            searcher=openalex_searcher,
            candidate_limit=200,
        ),
        huggingface=providers.Factory(
            SearchBinding,
            searcher=huggingface_provider,
        ),
    )
    lookup_bindings = providers.Dict(
        arxiv=providers.Factory(
            LookupBinding,
            provider=arxiv_searcher,
            cache=arxiv_cache,
            identifier_kinds=[IdentifierKind.ARXIV, IdentifierKind.DOI],
            has_signals=arxiv.has_signals,
        ),
        s2=providers.Factory(
            LookupBinding,
            provider=s2_provider,
            cache=s2_cache,
            identifier_kinds=[
                IdentifierKind.ARXIV,
                IdentifierKind.DOI,
                IdentifierKind.CORPUS_ID,
                IdentifierKind.S2,
            ],
            has_signals=s2.has_signals,
            aliases=s2.aliases,
            short_lived=s2.short_lived,
        ),
        openalex=providers.Factory(
            LookupBinding,
            provider=openalex_searcher,
            cache=openalex_cache,
            identifier_kinds=[
                IdentifierKind.OPENALEX,
                IdentifierKind.DOI,
                IdentifierKind.ARXIV,
            ],
            has_signals=openalex.has_signals,
            aliases=openalex.aliases,
        ),
        huggingface=providers.Factory(
            LookupBinding,
            provider=huggingface_provider,
            cache=huggingface_cache,
            identifier_kinds=[IdentifierKind.ARXIV],
            fields=[
                "ai_summary",
                "ai_keywords",
                "upvotes",
                "github_repo",
                "github_stars",
                "linked_models",
                "linked_datasets",
            ],
            has_signals=huggingface.has_signals,
        ),
    )
    lookup_runner = providers.Singleton(
        LookupRunner,
        bindings=lookup_bindings,
        positive_ttl_seconds=config.SCHOLAR_PAPER_CACHE_TTL_SECONDS,
        negative_ttl_seconds=config.SCHOLAR_NEGATIVE_CACHE_TTL_SECONDS,
    )
    paper_search_service = providers.Singleton(
        PaperSearchService,
        searchers=search_bindings,
        policies=providers.Object(FIELD_POLICIES),
        platform_credentials=platform_credentials,
        query_cache=query_cache_repository,
        lookup=lookup_runner,
    )
    paper_lookup_service = providers.Singleton(
        PaperLookupService,
        lookup=lookup_runner,
        policies=providers.Object(FIELD_POLICIES),
        platform_credentials=platform_credentials,
    )
    paper_discovery_service = providers.Singleton(
        PaperDiscoveryService,
        discoverers=providers.Dict(huggingface=huggingface_provider),
        policies=providers.Object(FIELD_POLICIES),
        platform_credentials=platform_credentials,
        cache=discovery_cache_repository,
    )


container = Container()
