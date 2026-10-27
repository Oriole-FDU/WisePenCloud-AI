"""Scholar 的依赖装配；应用用例只依赖声明的领域端口。"""

import httpx
from dependency_injector import containers, providers
from redis.asyncio import Redis

from scholar.application.field_policies import FIELD_POLICIES
from scholar.application.lookup import PaperLookupService
from scholar.application.search import PaperSearchService, SearchBinding
from scholar.application.search.enrichment import EnrichmentBinding
from scholar.core.storage.elasticsearch.paper_repository import (
    ElasticsearchPaperRepository,
    create_elasticsearch,
)
from scholar.core.storage.redis.paper_cache import RedisPaperEnrichmentCache
from scholar.core.storage.redis.query_cache_repository import RedisQueryCacheRepository
from scholar.domain.models.identifiers import IdentifierKind
from scholar.providers.arxiv import ArxivProvider
from scholar.providers.huggingface import HuggingFaceProvider
from scholar.providers.openalex import OpenAlexProvider


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
    paper_repository = providers.Singleton(
        ElasticsearchPaperRepository, es=elasticsearch, alias=config.SCHOLAR_INDEX_ALIAS
    )
    query_cache_repository = providers.Singleton(
        RedisQueryCacheRepository,
        redis=redis,
        namespace=config.SCHOLAR_INDEX_ALIAS,
        ttl=config.SCHOLAR_QUERY_CACHE_TTL_SECONDS,
    )
    arxiv_searcher = providers.Singleton(
        ArxivProvider, paper_repository=paper_repository
    )
    openalex_searcher = providers.Singleton(OpenAlexProvider, http=openalex_http)
    huggingface_provider = providers.Singleton(
        HuggingFaceProvider,
        http=huggingface_http,
        token=config.HF_TOKEN,
        concurrency=config.SCHOLAR_HF_CONCURRENCY,
    )
    s2_cache = providers.Singleton(
        RedisPaperEnrichmentCache,
        redis=redis,
        namespace=providers.Callable(
            lambda alias: f"{alias}:s2", config.SCHOLAR_INDEX_ALIAS
        ),
    )
    huggingface_cache = providers.Singleton(
        RedisPaperEnrichmentCache,
        redis=redis,
        namespace=providers.Callable(
            lambda alias: f"{alias}:huggingface", config.SCHOLAR_INDEX_ALIAS
        ),
    )
    search_bindings = providers.Dict(
        arxiv=providers.Factory(
            SearchBinding,
            searcher=arxiv_searcher,
            coverage="arXiv 本地记录；按领域映射召回，不代表整个出版体系。",
        ),
        openalex=providers.Factory(
            SearchBinding,
            searcher=openalex_searcher,
            requires_api_key=True,
            coverage="OpenAlex 按请求领域映射召回。",
        ),
    )
    enrichment_bindings = providers.Dict(
        arxiv=providers.Factory(
            EnrichmentBinding,
            enricher=arxiv_searcher,
            cache=None,
            identifier_kinds=[IdentifierKind.ARXIV, IdentifierKind.DOI],
            coverage="已有本地论文事实。",
        ),
        s2=providers.Factory(
            EnrichmentBinding,
            enricher=None,
            cache=s2_cache,
            identifier_kinds=[
                IdentifierKind.ARXIV,
                IdentifierKind.DOI,
                IdentifierKind.CORPUS_ID,
                IdentifierKind.S2,
            ],
            coverage="已有公共 S2 缓存；此路径不调用在线 S2。",
        ),
        huggingface=providers.Factory(
            EnrichmentBinding,
            enricher=huggingface_provider,
            cache=huggingface_cache,
            identifier_kinds=[IdentifierKind.ARXIV],
            coverage="HF 社区及研究产物事实。",
            fields=[
                "upvotes",
                "github_repo",
                "github_stars",
                "linked_models",
                "num_total_models",
                "linked_datasets",
                "num_total_datasets",
            ],
        ),
    )
    paper_search_service = providers.Singleton(
        PaperSearchService,
        searchers=search_bindings,
        policies=providers.Object(FIELD_POLICIES),
        query_cache=query_cache_repository,
        enrichment_bindings=enrichment_bindings,
        positive_ttl_seconds=config.SCHOLAR_PAPER_CACHE_TTL_SECONDS,
        negative_ttl_seconds=config.SCHOLAR_NEGATIVE_CACHE_TTL_SECONDS,
        concurrency=config.SCHOLAR_HF_CONCURRENCY,
    )
    paper_lookup_service = providers.Factory(
        PaperLookupService,
        paper_repository=paper_repository,
        openalex_lookup=openalex_searcher.provided.lookup,
    )


container = Container()
