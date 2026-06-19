# src/chat/container.py

from typing import List

import httpx
import hishel.httpx as hishel_httpx
from dependency_injector import containers, providers
from github import Auth, Github
from v2.nacos import NacosNamingService

from chat.application.agents import (
    DefaultAgentResolver,
)
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.tools.common.tool_content_store.store import (
    DEFAULT_TOOL_CONTENT_TTL_SECONDS,
    ToolContentStore,
)
from chat.application.tools.common.tool_run_file_store import ToolRunFileStore
from chat.application.tools.common.tool_run_file_store.gc import (
    ToolRunFileStoreGcScheduler,
)
from chat.application.tools.core import ToolRegistry
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.document_tools.document_parse import (
    DocumentParsePlanner,
    DocumentParseService,
)
from chat.application.tools.document_tools.document_parse.parsers.ocr import (
    PaddleCloudClient,
    PaddleCloudConfig,
)
from chat.application.tools.document_tools.document_parse_tool import DocumentParseTool
from chat.application.tools.math_tools.calculus_solver_tool import CalculusSolverTool
from chat.application.tools.math_tools.equation_solver_tool import EquationSolverTool
from chat.application.tools.math_tools.expression_solver_tool import ExpressionSolverTool
from chat.application.tools.math_tools.linear_algebra_solver_tool import LinearAlgebraSolverTool
from chat.application.tools.math_tools.stats_solver_tool import StatsSolverTool
from chat.application.tools.session_tools.get_historical_chat_messages_tool import (
    GetHistoricalChatMessagesTool,
)
from chat.application.tools.session_tools.tool_content_sequential_read_tool import (
    ToolContentSequentialReadTool,
)
from chat.application.tools.session_tools.tool_content_read_tool import ToolContentReadTool
from chat.application.tools.skill_tools import CreateSkillTool, LoadSkillAssetTool, LoadSkillTool
from chat.application.tools.skill_tools.create_skill.skill_publisher import SkillPublisher
from chat.application.tools.core import ToolExecutionError
from chat.application.tools.skill_tools.utils.skill_matcher import DefaultSkillMatcher
from chat.application.tools.tool_output_cache import ToolOutputCache
from chat.application.tools.tool_output_renderer import ToolOutputRenderer
from chat.application.tools.tool_settings import tool_settings
from chat.application.tools.utils.markdown_renderer import (
    FragmentMarkdownRenderer,
    TableMarkdownRenderer,
    WebPageMarkdownRenderer,
)
from chat.application.tools.web_tools.github_hydrate_tool import GitHubHydrateTool
from chat.application.tools.web_tools.hydrators import (
    GitHubHydrator,
    PaperHydrator,
)
from chat.application.tools.web_tools.paper_hydrate_tool import PaperHydrateTool
from chat.application.tools.web_tools.web_crawl_tool import WebCrawlTool
from chat.application.tools.web_tools.web_fetch import (
    FetchCoordinator,
    WebCrawlService,
)
from chat.application.tools.web_tools.web_fetch.cleaners.trafilatura_cleaner import (
    TrafilaturaCleaner,
)
from chat.application.tools.web_tools.web_fetch.fetchers.httpx_fetcher import (
    HttpxFetcher,
)
from chat.application.tools.web_tools.web_fetch.fetchers.scrapling_fetcher import (
    ScraplingFetcher,
)
from chat.application.tools.web_tools.web_fetch_tool import WebFetchTool
from chat.application.tools.web_tools.web_content_cache.gc import (
    WebContentCacheGcScheduler,
)
from chat.application.tools.web_tools.web_search.providers.models import SearchProviderName
from chat.application.tools.web_tools.web_search.runtime_context import (
    WebSearchRuntimeContextResolver,
)
from chat.application.tools.web_tools.web_search.searcher import WebSearchProviderSearcher
from chat.application.tools.web_tools.web_search.searchers import (
    DdgSearcher,
    ExaSearcher,
    FouGetDdgSearcher,
    FourGetSearcher,
    ProviderSearcher,
    SearchProviderConfig,
)
from chat.application.tools.web_tools.web_search.service import (
    WebSearchCustomSourceFactory,
    WebSearchService,
)
from chat.application.tools.web_tools.web_search_tool import WebSearchTool
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.config.nacos import nacos_client_manager
from chat.core.persistence.mongo.message_repository import MongoMessageRepository
from chat.core.persistence.mongo.model_repository import MongoModelRepository
from chat.core.persistence.mongo.provider_repository import MongoProviderRepository
from chat.core.persistence.mongo.session_repository import MongoSessionRepository
from chat.core.persistence.mongo.web_search_credential_repository import (
    MongoWebSearchCredentialRepository,
)
from chat.core.persistence.redis.hot_context import RedisHotContext
from chat.core.persistence.redis.tool_content_repository import RedisToolContentRepository
from chat.core.persistence.redis.tool_run_file_repository import RedisToolRunFileRepository
from chat.core.persistence.redis.web_content_cache_repository import (
    RedisMongoWebContentCacheRepository,
)
from chat.core.persistence.redis.web_content_cache_refresh_queue import (
    ArqWebContentCacheRefreshTaskPublisher,
)
from chat.core.persistence.redis.web_search_candidate_repository import (
    RedisWebSearchCandidateRepository,
)
from chat.core.providers import (
    LiteLLMAdapter,
    Mem0Adapter,
    OssFileLoader,
)
from chat.core.security import SecretCipher
from chat.service_client import FileStorageClient, AIAssetClient, ResourceClient
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


def _build_registry(tool_providers: List[providers.Provider]) -> ToolRegistry:
    registry = ToolRegistry()
    for provider in tool_providers:
        registry.register(provider)
    return registry


def _build_paddle_ocr_client(
    *,
    http_client: httpx.AsyncClient,
) -> PaddleCloudClient | None:
    if not settings.PADDLE_OCR_TOKEN:
        return None

    return PaddleCloudClient(
        config=PaddleCloudConfig(
            api_url=settings.PADDLE_OCR_API_URL,
            token=settings.PADDLE_OCR_TOKEN,
            model=settings.PADDLE_OCR_MODEL,
            timeout_seconds=tool_settings.PADDLE_OCR_TIMEOUT_SECONDS,
            poll_interval_seconds=tool_settings.PADDLE_OCR_POLL_INTERVAL_SECONDS,
            max_poll_attempts=tool_settings.PADDLE_OCR_MAX_POLL_ATTEMPTS,
        ),
        http_client=http_client,
    )


def _build_platform_web_searcher(
    *,
    http_client: httpx.AsyncClient,
) -> WebSearchProviderSearcher:
    provider_searchers: dict[SearchProviderName, ProviderSearcher] = {
        SearchProviderName.FOUGET_DDG: FouGetDdgSearcher(
            fourget_searcher=FourGetSearcher(
                http_client=http_client,
                config=SearchProviderConfig(
                    base_url=settings.WEB_SEARCH_FOURGET_BASE_URL,
                    source_id="platform:4get_ddg",
                ),
            ),
            ddg_searcher=DdgSearcher(),
        ),
    }
    if settings.WEB_SEARCH_PLATFORM_EXA_ENABLED and settings.WEB_SEARCH_PLATFORM_EXA_API_KEY:
        provider_searchers[SearchProviderName.EXA] = ExaSearcher(
            http_client=http_client,
            config=SearchProviderConfig(
                base_url=settings.WEB_SEARCH_EXA_BASE_URL,
                api_key=settings.WEB_SEARCH_PLATFORM_EXA_API_KEY,
                source_id="platform:exa",
            ),
        )
    return WebSearchProviderSearcher(provider_searchers=provider_searchers)


def _build_web_fetch_http_client() -> httpx.AsyncClient:
    transport = hishel_httpx.AsyncCacheTransport(
        next_transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
    )
    return httpx.AsyncClient(
        timeout=httpx.Timeout(tool_settings.WEB_FETCH_TIMEOUT_SECONDS),
        transport=transport,
        trust_env=False,
    )


def _build_github_api_client() -> Github:
    return Github(auth=Auth.Token(settings.GITHUB_TOKEN))


class _PlaceholderSkillPublisher(SkillPublisher):
    async def publish(
        self,
        *,
        skill_id: str,
        title: str,
        trigger_description: str,
        markdown: str,
        user_id: str,
        session_id: str,
    ):
        raise ToolExecutionError(
            reason="skill_publish_not_available",
            detail_reason=(
                "Skill publishing is not yet available. "
                "The create_skill tool is registered but the publish backend is not connected."
            ),
            retryable=False,
        )


class Container(containers.DeclarativeContainer):
    """依赖注入容器，管理单例对象的生命周期。"""
    llm_provider = providers.Singleton(LiteLLMAdapter)
    memory_provider = providers.Singleton(Mem0Adapter)

    session_repo = providers.Singleton(MongoSessionRepository)
    message_repo = providers.Singleton(MongoMessageRepository)
    model_repo = providers.Singleton(MongoModelRepository)
    provider_repo = providers.Singleton(MongoProviderRepository)
    secret_cipher = providers.Singleton(
        SecretCipher,
        encryption_key=settings.SECRET_ENCRYPTION_KEY,
    )
    web_search_credential_repo = providers.Singleton(
        MongoWebSearchCredentialRepository,
        secret_cipher=secret_cipher,
    )
    hot_context_repo = providers.Singleton(RedisHotContext)

    # 内部 RPC：Nacos 服务发现 + 通用 httpx 客户端 + file-storage typed facade
    service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=settings.RPC_LB_STRATEGY,
        cache_ttl_seconds=settings.SERVICE_DISCOVERY_CACHE_TTL_SECONDS,
    )
    rpc_client = providers.Singleton(
        RpcClient,
        discovery=service_discovery,
        from_source_secret=settings.FROM_SOURCE_SECRET,
        timeout=settings.RPC_DEFAULT_TIMEOUT,
        retries=settings.RPC_DEFAULT_RETRIES,
        default_strategy=settings.RPC_LB_STRATEGY,
    )
    file_storage_client = providers.Singleton(
        FileStorageClient,
        rpc=rpc_client,
    )
    ai_asset_client = providers.Singleton(
        AIAssetClient,
        rpc=rpc_client,
    )
    resource_client = providers.Singleton(
        ResourceClient,
        rpc=rpc_client,
    )

    # OssFileLoader
    oss_file_loader = providers.Singleton(
        OssFileLoader,
        file_storage_client=file_storage_client,
        cache_dir=settings.OSS_CACHE_DIR,
        cache_ttl_seconds=settings.OSS_CACHE_TTL_SECONDS,
        gc_interval_seconds=settings.OSS_CACHE_GC_INTERVAL_SECONDS,
    )

    # Skill 子系统：
    # - SkillRepository 从 Java ai-asset 读取 Skill
    # DefaultSkillMatcher
    skill_matcher = providers.Singleton(
        DefaultSkillMatcher,
        ai_asset_client=ai_asset_client,
    )
    agent_resolver = providers.Singleton(DefaultAgentResolver)
    kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )

    # ==================================================================
    # Tool 基础设施：仓储、存储、缓存、渲染、调度
    # ==================================================================
    tool_content_repository = providers.Singleton(
        RedisToolContentRepository,
        redis_url=settings.REDIS_URL,
        ttl_seconds=DEFAULT_TOOL_CONTENT_TTL_SECONDS,
    )
    tool_content_store = providers.Singleton(
        ToolContentStore,
        repository=tool_content_repository,
    )
    tool_run_file_repository = providers.Singleton(
        RedisToolRunFileRepository,
        redis_url=settings.REDIS_URL,
    )
    tool_run_file_store = providers.Singleton(
        ToolRunFileStore,
        repository=tool_run_file_repository,
        root_dir=settings.TOOL_RUN_FILE_ROOT,
        ref_ttl_seconds=tool_settings.TOOL_RUN_FILE_REF_TTL_SECONDS,
        cleanup_grace_seconds=tool_settings.TOOL_RUN_FILE_CLEANUP_GRACE_SECONDS,
        max_file_size_bytes=tool_settings.TOOL_RUN_FILE_MAX_BYTES,
    )
    tool_run_file_store_gc_scheduler = providers.Singleton(
        ToolRunFileStoreGcScheduler,
        store=tool_run_file_store,
    )
    tool_output_renderer = providers.Singleton(ToolOutputRenderer)
    tool_output_cache = providers.Singleton(
        ToolOutputCache,
        content_store=tool_content_store,
        inline_max_chars=settings.TOOL_RESULT_MAX_CHARS,
    )
    tool_dispatcher = providers.Singleton(
        ToolDispatcher,
        output_renderer=tool_output_renderer,
        output_cache=tool_output_cache,
    )

    # ==================================================================
    # Tool 组件：HTTP 客户端、Fetcher、Searcher、Service、Hydrator 等
    # ==================================================================

    # --- Document Parse 组件 ---
    paddle_ocr_http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=httpx.Timeout(tool_settings.PADDLE_OCR_TIMEOUT_SECONDS),
    )
    paddle_ocr_client = providers.Singleton(
        _build_paddle_ocr_client,
        http_client=paddle_ocr_http_client,
    )
    document_parse_planner = providers.Singleton(
        DocumentParsePlanner,
        ocr_client=paddle_ocr_client,
        table_renderer=providers.Factory(TableMarkdownRenderer),
    )
    document_parse_service = providers.Singleton(
        DocumentParseService,
        planner=document_parse_planner,
    )

    # --- Web Search 组件 ---
    web_search_http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=httpx.Timeout(tool_settings.WEB_SEARCH_TIMEOUT_SECONDS),
        trust_env=False,
    )
    platform_web_searcher = providers.Singleton(
        _build_platform_web_searcher,
        http_client=web_search_http_client,
    )
    web_search_runtime_context_resolver = providers.Singleton(
        WebSearchRuntimeContextResolver,
        credential_repository=web_search_credential_repo,
        cipher=secret_cipher,
        platform_exa_enabled=settings.WEB_SEARCH_PLATFORM_EXA_ENABLED,
        platform_exa_api_key=settings.WEB_SEARCH_PLATFORM_EXA_API_KEY,
    )
    web_search_custom_source_factory = providers.Singleton(
        WebSearchCustomSourceFactory,
        http_client=web_search_http_client,
        exa_base_url=settings.WEB_SEARCH_EXA_BASE_URL,
        tavily_base_url=settings.WEB_SEARCH_TAVILY_BASE_URL,
        anysearch_base_url=settings.WEB_SEARCH_ANYSEARCH_BASE_URL,
        serper_base_url=settings.WEB_SEARCH_SERPER_BASE_URL,
    )
    web_search_candidate_repository = providers.Singleton(
        RedisWebSearchCandidateRepository,
        redis_url=settings.REDIS_URL,
    )
    web_search_service = providers.Singleton(
        WebSearchService,
        platform_searcher=platform_web_searcher,
    )

    # --- Web Fetch / Crawl 组件 ---
    web_content_cache_repository = providers.Singleton(
        RedisMongoWebContentCacheRepository,
        redis_url=settings.REDIS_URL,
    )
    web_content_cache_gc_scheduler = providers.Singleton(
        WebContentCacheGcScheduler,
        repository=web_content_cache_repository,
    )
    web_content_cache_refresh_task_publisher = providers.Singleton(
        ArqWebContentCacheRefreshTaskPublisher,
        redis_url=settings.REDIS_URL,
    )
    web_fetch_http_client = providers.Singleton(
        _build_web_fetch_http_client,
    )
    web_fetch_httpx_fetcher = providers.Singleton(
        HttpxFetcher,
        http_client=web_fetch_http_client,
        max_response_bytes=tool_settings.WEB_FETCH_MAX_RESPONSE_BYTES,
    )
    web_fetch_scrapling_fetcher = providers.Singleton(
        ScraplingFetcher,
        timeout_ms=int(tool_settings.WEB_FETCH_TIMEOUT_SECONDS * 1000),
        max_response_bytes=tool_settings.WEB_FETCH_MAX_RESPONSE_BYTES,
    )
    web_fetch_cleaner = providers.Singleton(
        TrafilaturaCleaner,
        renderer=providers.Singleton(WebPageMarkdownRenderer),
    )
    web_crawl_service = providers.Singleton(
        WebCrawlService,
        httpx_fetcher=web_fetch_httpx_fetcher,
        scrapling_fetcher=web_fetch_scrapling_fetcher,
        cleaner=web_fetch_cleaner,
        content_cache_repository=web_content_cache_repository,
        refresh_task_publisher=web_content_cache_refresh_task_publisher,
        min_text_length=tool_settings.WEB_FETCH_MIN_TEXT_LENGTH,
        concurrency=tool_settings.WEB_FETCH_BATCH_CONCURRENCY,
    )
    web_fetch_coordinator = providers.Singleton(
        FetchCoordinator,
        httpx_fetcher=web_fetch_httpx_fetcher,
        scrapling_fetcher=web_fetch_scrapling_fetcher,
        cleaner=web_fetch_cleaner,
        file_store=tool_run_file_store,
        content_cache_repository=web_content_cache_repository,
        refresh_task_publisher=web_content_cache_refresh_task_publisher,
        min_text_length=tool_settings.WEB_FETCH_MIN_TEXT_LENGTH,
        batch_concurrency=tool_settings.WEB_FETCH_BATCH_CONCURRENCY,
    )

    # --- Hydrator 组件 ---
    paper_hydrate_service = providers.Singleton(
        PaperHydrator,
        http_client=web_search_http_client,
        api_key=settings.OPENALEX_API_KEY,
        base_url=settings.OPENALEX_BASE_URL,
    )
    github_api_client = providers.Singleton(
        _build_github_api_client,
    )
    github_hydrate_service = providers.Singleton(
        GitHubHydrator,
        github_client=github_api_client,
    )

    # --- Skill 组件 ---
    _placeholder_skill_publisher = providers.Singleton(_PlaceholderSkillPublisher)

    # ==================================================================
    # Tool 本身：最终注册到 ToolRegistry 的工具实例
    # ==================================================================

    # --- Math Tools ---
    calculus_solver_tool = providers.Singleton(CalculusSolverTool)
    linear_algebra_solver_tool = providers.Singleton(LinearAlgebraSolverTool)
    equation_solver_tool = providers.Singleton(EquationSolverTool)
    stats_solver_tool = providers.Singleton(StatsSolverTool)
    expression_solver_tool = providers.Singleton(ExpressionSolverTool)

    # --- Document Tools ---
    document_parse_tool = providers.Singleton(
        DocumentParseTool,
        file_store=tool_run_file_store,
        parse_service=document_parse_service,
        content_cache_repository=web_content_cache_repository,
        refresh_task_publisher=web_content_cache_refresh_task_publisher,
        direct_fetcher=web_fetch_httpx_fetcher,
    )

    # --- Session Tools ---
    search_history_tool = providers.Singleton(
        GetHistoricalChatMessagesTool,
        message_repo=message_repo,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    )
    tool_content_read_tool = providers.Singleton(
        ToolContentReadTool,
        content_store=tool_content_store,
    )
    tool_content_sequential_read_tool = providers.Singleton(
        ToolContentSequentialReadTool,
        content_store=tool_content_store,
    )

    # --- Web Tools ---
    web_search_tool = providers.Singleton(
        WebSearchTool,
        service=web_search_service,
        custom_source_factory=web_search_custom_source_factory,
        candidate_repository=web_search_candidate_repository,
        max_hops=3,
    )
    web_crawl_tool = providers.Singleton(
        WebCrawlTool,
        service=web_crawl_service,
    )
    web_fetch_tool = providers.Singleton(
        WebFetchTool,
        service=web_fetch_coordinator,
        candidate_repository=web_search_candidate_repository,
    )
    paper_hydrate_tool = providers.Singleton(
        PaperHydrateTool,
        service=paper_hydrate_service,
    )
    github_hydrate_tool = providers.Singleton(
        GitHubHydrateTool,
        service=github_hydrate_service,
    )

    # --- Skill Tools ---
    load_skill_tool = providers.Singleton(
        LoadSkillTool,
        ai_asset_client=ai_asset_client,
        resource_client=resource_client,
        file_loader=oss_file_loader,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    )
    load_skill_asset_tool = providers.Singleton(
        LoadSkillAssetTool,
        ai_asset_client=ai_asset_client,
        resource_client=resource_client,
        file_loader=oss_file_loader,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    )
    create_skill_tool = providers.Singleton(
        CreateSkillTool,
        skill_publisher=_placeholder_skill_publisher,
    )

    # --- Tool Registry ---
    tool_providers = providers.List(
        document_parse_tool,
        calculus_solver_tool,
        linear_algebra_solver_tool,
        equation_solver_tool,
        stats_solver_tool,
        expression_solver_tool,
        tool_content_read_tool,
        tool_content_sequential_read_tool,
        paper_hydrate_tool,
        github_hydrate_tool,
        web_search_tool,
        web_crawl_tool,
        web_fetch_tool,
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
        create_skill_tool,
    )
    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
    )

    # Application 层组件
    chat_turn_coordinator = providers.Factory(
        ChatTurnCoordinator,
        llm=llm_provider,
        memory=memory_provider,
        model_repo=model_repo,
        provider_repo=provider_repo,
        session_repo=session_repo,
        message_repo=message_repo,
        hot_context_repo=hot_context_repo,
        tool_registry=tool_registry,
        tool_dispatcher=tool_dispatcher,
        kafka_producer=kafka_producer,
        skill_matcher=skill_matcher,
        agent_resolver=agent_resolver,
        web_search_runtime_context_resolver=web_search_runtime_context_resolver,
    )


# 全局容器实例
container = Container()
