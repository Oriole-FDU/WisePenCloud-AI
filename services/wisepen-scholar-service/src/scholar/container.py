"""Scholar 的依赖装配；应用用例只依赖声明的领域端口。"""

from dependency_injector import containers, providers
from redis.asyncio import Redis

from scholar.application.lookup import PaperLookupService
from scholar.application.search import PaperSearchService
from scholar.core.storage.elasticsearch.paper_repository import (
    ElasticsearchPaperRepository,
    create_elasticsearch,
)
from scholar.core.storage.redis.query_cache_repository import RedisQueryCacheRepository


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
    paper_repository = providers.Singleton(
        ElasticsearchPaperRepository, es=elasticsearch, alias=config.SCHOLAR_INDEX_ALIAS
    )
    query_cache_repository = providers.Singleton(
        RedisQueryCacheRepository,
        redis=redis,
        namespace=config.SCHOLAR_INDEX_ALIAS,
        ttl=config.SCHOLAR_QUERY_CACHE_TTL_SECONDS,
    )
    paper_search_service = providers.Factory(
        PaperSearchService,
        paper_repository=paper_repository,
        query_cache=query_cache_repository,
    )
    paper_lookup_service = providers.Factory(
        PaperLookupService, paper_repository=paper_repository
    )


container = Container()
