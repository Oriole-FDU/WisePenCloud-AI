"""Tool 生态依赖注入注册。

该模块包揽工具相关 provider 的装配，包括 document、web、math、
language、browser 运行时依赖，以及最终 Tool Registry。主容器只负责
在核心基础设施和 RAG 注册完成后调用本模块入口。
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List

import httpx
from dependency_injector import providers
from docling.document_converter import DocumentConverter
from markitdown import MarkItDown
from paddleocr import PPStructure
from steel import AsyncSteel

from chat.application.api_service.document_export import DocumentExportDownloadService
from chat.application.infra.content_store import ContentStore
from chat.application.infra.content_store.redis_repository import RedisContentRepository
from chat.application.infra.document_temp_files.cleanup import (
    DOCUMENT_TEMP_FILE_TTL_SECONDS,
    DocumentTempFileCleanupService,
)
from chat.application.infra.document_temp_files.resolver import DocumentTempFileResolver
from chat.application.infra.document_temp_files.scheduler import (
    DocumentTempFileCleanupScheduler,
)
from chat.application.tool_output_aspect import ToolOutputAspect
from chat.application.tool_registry import ToolRegistry
from chat.application.tools.browser.browse_interact_tool import BrowseInteractTool
from chat.application.tools.chart.function_plot_tool import FunctionPlotTool
from chat.application.tools.chart.services.function_plot.analyzer import (
    FunctionFeatureAnalyzer,
)
from chat.application.tools.chart.services.function_plot.exporter import FunctionPlotExporter
from chat.application.tools.chart.services.function_plot.parser import (
    FunctionExpressionParser,
)
from chat.application.tools.chart.services.function_plot.renderer import FunctionPlotRenderer
from chat.application.tools.chart.services.function_plot.sampler import FunctionSampler
from chat.application.tools.chart.services.function_plot.service import FunctionPlotService
from chat.application.tools.chart.services.stat_chart.dataframe import DataFrameBuilder
from chat.application.tools.chart.services.stat_chart.exporter import StatChartExporter
from chat.application.tools.chart.services.stat_chart.renderer import StatChartRenderer
from chat.application.tools.chart.services.stat_chart.service import StatChartService
from chat.application.tools.chart.services.stat_chart.validator import StatChartSpecValidator
from chat.application.tools.chart.stat_chart_tool import StatChartTool
from chat.application.tools.document.document_convert_tool import DocumentConvertTool
from chat.application.tools.document.document_export_tool import DocumentExportTool
from chat.application.tools.document.document_parse_tool import DocumentParseTool
from chat.application.tools.document.services.document_convert import DocumentConvertService
from chat.application.tools.document.services.document_convert.converter import MarkdownConverter
from chat.application.tools.document.services.document_export.runtime.atomic_writer import (
    AtomicExportWriter,
)
from chat.application.tools.document.services.document_export.runtime.download_resolver import (
    DocumentDownloadResolver,
)
from chat.application.tools.document.services.document_export.runtime.playwright_pool import (
    PlaywrightBrowserPool,
)
from chat.application.tools.document.services.document_export.runtime.renderers.docx_renderer import (
    DocxRenderer,
)
from chat.application.tools.document.services.document_export.runtime.renderers.html_renderer import (
    HtmlRenderer,
)
from chat.application.tools.document.services.document_export.runtime.renderers.markdown_renderer import (
    MarkdownRenderer,
)
from chat.application.tools.document.services.document_export.runtime.renderers.pdf_renderer import (
    PdfRenderer,
)
from chat.application.tools.document.services.document_export.runtime.renderers.txt_renderer import (
    TxtRenderer,
)
from chat.application.tools.document.services.document_export.service import DocumentExportService
from chat.application.tools.document.services.document_export.utils.path import (
    document_export_output_path,
)
from chat.application.tools.document.services.document_parse import DocumentParseService
from chat.application.tools.document.services.document_parse.ocr.image_adapter import (
    OcrImageAdapter,
)
from chat.application.tools.document.services.document_parse.ocr.processor import (
    OcrProcessor,
    OcrProcessorConfig,
)
from chat.application.tools.document.services.document_parse.parsers.epub import EpubParser
from chat.application.tools.document.services.document_parse.parsers.office.fallback import (
    OfficeFallbackParser,
)
from chat.application.tools.document.services.document_parse.parsers.office.parser import OfficeParser
from chat.application.tools.document.services.document_parse.parsers.office.primary import (
    OfficePrimaryParser,
)
from chat.application.tools.document.services.document_parse.parsers.pdf.docling import (
    DoclingPdfExtractor,
)
from chat.application.tools.document.services.document_parse.parsers.pdf.page_classifier import (
    PageClassifier,
)
from chat.application.tools.document.services.document_parse.parsers.pdf.parser import PdfParser
from chat.application.tools.document.services.document_parse.parsers.pdf.table_extractor import (
    TableExtractor,
)
from chat.application.tools.document.services.document_parse.parsers.spreadsheet import (
    SpreadsheetParser,
)
from chat.application.tools.evidence_access.evidence_rank_tool import EvidenceRankTool
from chat.application.tools.evidence_access.tool_content_batch_read_tool import (
    ToolContentBatchReadTool,
)
from chat.application.tools.evidence_access.tool_content_read_tool import ToolContentReadTool
from chat.application.tools.language.services.translation.runtime.model_provider import (
    OpusMtModelProvider,
)
from chat.application.tools.language.services.translation.runtime.opus_mt_engine import (
    OpusMtEngineConfig,
    OpusMtTranslationEngine,
)
from chat.application.tools.language.services.translation.service import (
    TranslationAssistService,
)
from chat.application.tools.language.translation_assist_tool import TranslationAssistTool
from chat.application.tools.math_solver.python_math_solver_tool import PythonMathSolverTool
from chat.application.tools.math_solver.sage_math_solver_tool import SageMathSolverTool
from chat.application.tools.math_solver.services.python_runtime.engine import PythonMathEngine
from chat.application.tools.math_solver.services.python_runtime.service import (
    PythonMathSolverService,
)
from chat.application.tools.math_solver.services.sage_runtime.client import SageRuntimeClient
from chat.application.tools.math_solver.services.sage_runtime.service import SageMathSolverService
# RAG 通道暂时关闭：保留导入位置，恢复时取消注释即可。
# from chat.application.tools.retrieval.rag_search_tool import RagSearchTool
from chat.application.tools.retrieval.search_history_tool import SearchHistoricalMessagesTool
from chat.application.tools.skill.load_skill_asset_tool import LoadSkillAssetTool
from chat.application.tools.skill.load_skill_tool import LoadSkillTool
from chat.application.tools.skill.services.skill_create.service import (
    DevSkillBundleArtifactStore,
    SkillBundleService,
    SkillMarkdownRenderer,
)
from chat.application.tools.skill.skill_create_tool import CreateSkillBundleTool
from chat.application.tools.text.count_text_chars_tool import CountTextCharsTool
from chat.application.tools.tool_content_store import (
    ToolContentStore,
    _TOOL_CONTENT_STORE_MAX_ITEM_CHARS,
    _TOOL_CONTENT_STORE_TTL_SECONDS,
)
from chat.application.tools.tool_search_tool import ToolSearchTool
from chat.application.tools.web.services.common.file_handoff.store import (
    DEFAULT_HANDOFF_ROOT,
    TemporaryFileHandoffStore,
)
from chat.application.tools.web.services.common.file_type_detection.detector import FileTypeDetector
from chat.application.tools.web.services.common.file_type_detection.magika import MagikaDetector
from chat.application.tools.web.services.web_crawl import WebCrawlService
from chat.application.tools.web.services.web_fetch import FetchCoordinator
from chat.application.tools.web.services.web_fetch.fetcher.content_processor import ContentProcessor
from chat.application.tools.web.services.web_fetch.fetcher.local_fetcher import LocalScriptFetcher
from chat.application.tools.web.services.web_fetch.fetcher.static_fetcher import StaticFetcher
from chat.application.tools.web.services.web_fetch.fetcher.steel_fetcher import (
    SteelFetcher,
    SteelFetcherConfig,
)
from chat.application.tools.web.services.web_search.cache import SearchCache
from chat.application.tools.web.services.web_search.coordinator import SearchCoordinator
from chat.application.tools.web.services.web_search.enums import ProviderMode
from chat.application.tools.web.services.web_search.runtime.runner import CustomProviderRunner
from chat.application.tools.web.services.web_search.runtime.runner import FourGetSearchRunner
from chat.application.tools.web.services.web_search.runtime.runner.serper import SerperSearchRunner
from chat.application.tools.web.services.web_search.runtime.runner.wikipedia import WikipediaRunner
from chat.application.tools.web.services.web_search.searcher.fourget import FourGetSearcher
from chat.application.tools.web.services.web_search.searcher.serper import SerperSearcher
from chat.application.tools.web.services.web_search.searcher.wikipedia import WikipediaSearcher
from chat.application.tools.web.web_crawl_tool import WebCrawlTool
from chat.application.tools.web.web_fetch_tool import WebFetchTool
from chat.application.tools.web.web_search_tool import WebSearchTool
from chat.core.config.app_settings import settings
from chat.core.config.tool_settings import tool_settings


@asynccontextmanager
async def _web_search_http_client() -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient()
    try:
        yield client
    finally:
        await client.aclose()


@asynccontextmanager
async def _static_fetch_http_client() -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient(
        timeout=15.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        transport=httpx.AsyncHTTPTransport(retries=3),
        follow_redirects=False,
    )
    try:
        yield client
    finally:
        await client.aclose()


@asynccontextmanager
async def _steel_client(config: SteelFetcherConfig) -> AsyncIterator[AsyncSteel]:
    client = AsyncSteel(
        base_url=config.base_url,
        timeout=config.timeout,
        max_retries=0,
    )
    try:
        yield client
    finally:
        if not client.is_closed():
            await client.close()


def register_tools(container_cls: Any) -> None:
    """注册工具生态 provider 和所有 Tool。

    Args:
        container_cls: 主依赖注入容器类。调用前必须已注册 Tool 依赖的
            core、persistence、skill、rag 等基础 provider。
    """
    _register_document_parse(container_cls)
    _register_document_export(container_cls)
    _register_document_convert(container_cls)
    _register_tool_content(container_cls)
    _register_chart(container_cls)
    _register_web(container_cls)
    _register_skill_create(container_cls)

    tool_providers: List[providers.Provider] = []

    def tool(attr_name: str, *singleton_args: Any, **singleton_kwargs: Any) -> None:
        provider = providers.Singleton(*singleton_args, **singleton_kwargs)
        setattr(container_cls, attr_name, provider)
        tool_providers.append(provider)

    # ----- 核心系统工具 -----
    tool(
        "tool_search_tool",
        ToolSearchTool,
    )

    tool(
        "search_history_tool",
        SearchHistoricalMessagesTool,
        message_repo=container_cls.message_repo,
    )

    tool(
        "load_skill_tool",
        LoadSkillTool,
        skill_repo=container_cls.skill_repo,
    )

    tool(
        "load_skill_asset_tool",
        LoadSkillAssetTool,
        skill_repo=container_cls.skill_repo,
        skill_asset_loader=container_cls.skill_asset_loader,
    )

    # ----- 联网与知识检索 -----
    tool(
        "web_search_tool",
        WebSearchTool,
        coordinator=container_cls.web_search_coordinator,
        content_store=container_cls.tool_content_store,
    )

    tool(
        "web_fetch_tool",
        WebFetchTool,
        fetcher=container_cls.fetch_coordinator,
        file_handoff_store=container_cls.file_handoff_store,
        content_store=container_cls.tool_content_store,
    )

    tool(
        "web_crawl_tool",
        WebCrawlTool,
        service=container_cls.web_crawl_service,
    )

    # RAG 通道暂时关闭，避免 rag_service / indexing queue 在主聊天流程中被实例化。
    # tool(
    #     "rag_search_tool",
    #     RagSearchTool,
    #     rag_service=container_cls.rag_service,
    # )

    tool(
        "evidence_rank_tool",
        EvidenceRankTool,
        content_store=container_cls.tool_content_store,
    )

    # ----- 数理计算工具 -----
    container_cls.python_math_engine = providers.Singleton(PythonMathEngine)

    container_cls.python_math_solver_service = providers.Singleton(
        PythonMathSolverService,
        engine=container_cls.python_math_engine,
    )

    tool(
        "python_math_solver_tool",
        PythonMathSolverTool,
        service=container_cls.python_math_solver_service,
    )

    container_cls.sage_http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=tool_settings.SAGE_MATH_WORKER_TIMEOUT_SECONDS,
    )

    container_cls.sage_runtime_client = providers.Singleton(
        SageRuntimeClient,
        http_client=container_cls.sage_http_client,
        base_url=settings.SAGE_MATH_WORKER_URL,
    )

    container_cls.sage_math_solver_service = providers.Singleton(
        SageMathSolverService,
        client=container_cls.sage_runtime_client,
    )

    tool(
        "sage_math_solver_tool",
        SageMathSolverTool,
        service=container_cls.sage_math_solver_service,
    )

    # ----- 图表渲染工具 -----
    tool(
        "function_plot_tool",
        FunctionPlotTool,
        service=container_cls.function_plot_service,
    )

    tool(
        "stat_chart_tool",
        StatChartTool,
        service=container_cls.stat_chart_service,
    )

    # ----- 翻译工具 -----
    container_cls.translation_opus_config = providers.Singleton(OpusMtEngineConfig)

    container_cls.translation_model_provider = providers.Singleton(
        OpusMtModelProvider,
        config=container_cls.translation_opus_config,
    )

    container_cls.translation_engine = providers.Singleton(
        OpusMtTranslationEngine,
        model_provider=container_cls.translation_model_provider,
    )

    container_cls.translation_assist_service = providers.Singleton(
        TranslationAssistService,
        engine=container_cls.translation_engine,
    )

    tool(
        "translation_assist_tool",
        TranslationAssistTool,
        service=container_cls.translation_assist_service,
    )

    tool(
        "count_text_chars_tool",
        CountTextCharsTool,
    )

    tool(
        "browse_interact_tool",
        BrowseInteractTool,
        timeout=tool_settings.BROWSER_INTERACT_TIMEOUT_SECONDS,
        headless=tool_settings.BROWSER_INTERACT_HEADLESS,
        disable_sandbox=tool_settings.BROWSER_INTERACT_DISABLE_SANDBOX,
        disable_dev_shm_usage=tool_settings.BROWSER_INTERACT_DISABLE_DEV_SHM_USAGE,
    )

    # ----- 文档处理工具 -----
    tool(
        "document_parse_tool",
        DocumentParseTool,
        parse_service=container_cls.document_parse_service,
        temp_file_resolver=container_cls.document_file_resolver,
        content_store=container_cls.tool_content_store,
    )

    tool(
        "document_export_tool",
        DocumentExportTool,
        export_service=container_cls.document_export_service,
        content_store=container_cls.tool_content_store,
    )

    tool(
        "document_convert_tool",
        DocumentConvertTool,
        convert_service=container_cls.document_convert_service,
    )

    tool(
        "tool_content_read_tool",
        ToolContentReadTool,
        content_store=container_cls.tool_content_store,
    )

    tool(
        "tool_content_batch_read_tool",
        ToolContentBatchReadTool,
        content_store=container_cls.tool_content_store,
    )

    # ----- Skill 创建工具 -----
    tool(
        "skill_create_tool",
        CreateSkillBundleTool,
        skill_bundle_service=container_cls.skill_bundle_service,
    )

    # 注册顺序即 tool_providers 追加顺序；ToolRegistry 保持原有遍历顺序。
    container_cls.tool_instances = providers.List(*tool_providers)

    container_cls.tool_registry = providers.Singleton(
        _build_registry,
        tool_instances=container_cls.tool_instances,
    )


def _build_registry(tool_instances: List[Any]) -> ToolRegistry:
    """构建工具注册表单例。"""
    registry = ToolRegistry()
    for tool_instance in tool_instances:
        registry.register(tool_instance)
    return registry


def _register_tool_content(container_cls: Any) -> None:
    """注册 ToolContent 的 Redis 仓储、门面和统一输出切面。"""
    container_cls.tool_content_repository = providers.Singleton(
        RedisContentRepository,
        redis_url=settings.REDIS_URL,
        ttl_seconds=_TOOL_CONTENT_STORE_TTL_SECONDS,
    )

    container_cls.tool_content_base_store = providers.Singleton(
        ContentStore,
        repository=container_cls.tool_content_repository,
        default_chunk_size=settings.TOOL_RESULT_MAX_CHARS * 2,
        max_item_chars=_TOOL_CONTENT_STORE_MAX_ITEM_CHARS,
        normalize_text=True,
    )

    container_cls.tool_content_store = providers.Singleton(
        ToolContentStore,
        content_store=container_cls.tool_content_base_store,
    )

    container_cls.tool_output_aspect = providers.Singleton(
        ToolOutputAspect,
        content_store=container_cls.tool_content_store,
    )


def _register_chart(container_cls: Any) -> None:
    """注册图表工具依赖。"""
    container_cls.function_expression_parser = providers.Singleton(FunctionExpressionParser)
    container_cls.function_sampler = providers.Singleton(FunctionSampler)
    container_cls.function_feature_analyzer = providers.Singleton(FunctionFeatureAnalyzer)
    container_cls.function_plot_renderer = providers.Singleton(FunctionPlotRenderer)
    container_cls.function_plot_exporter = providers.Singleton(FunctionPlotExporter)
    container_cls.function_plot_service = providers.Singleton(
        FunctionPlotService,
        parser=container_cls.function_expression_parser,
        sampler=container_cls.function_sampler,
        analyzer=container_cls.function_feature_analyzer,
        renderer=container_cls.function_plot_renderer,
        exporter=container_cls.function_plot_exporter,
    )

    container_cls.stat_chart_dataframe_builder = providers.Singleton(DataFrameBuilder)
    container_cls.stat_chart_validator = providers.Singleton(StatChartSpecValidator)
    container_cls.stat_chart_renderer = providers.Singleton(StatChartRenderer)
    container_cls.stat_chart_exporter = providers.Singleton(StatChartExporter)
    container_cls.stat_chart_service = providers.Singleton(
        StatChartService,
        dataframe_builder=container_cls.stat_chart_dataframe_builder,
        validator=container_cls.stat_chart_validator,
        renderer=container_cls.stat_chart_renderer,
        exporter=container_cls.stat_chart_exporter,
    )


def _register_document_parse(container_cls: Any) -> None:
    components = _build_document_parse_components()

    container_cls.ocr_processor_config = providers.Object(components["ocr_processor_config"])
    container_cls.ocr_processor = providers.Object(components["ocr_processor"])
    container_cls.ocr_image_adapter = providers.Object(components["ocr_image_adapter"])
    container_cls.docling_document_converter = providers.Object(
        components["docling_document_converter"]
    )
    container_cls.markitdown_converter = providers.Object(components["markitdown_converter"])
    container_cls.pp_structure_engine = providers.Object(components["pp_structure_engine"])
    container_cls.office_primary_parser = providers.Object(components["office_primary_parser"])
    container_cls.office_fallback_parser = providers.Object(components["office_fallback_parser"])
    container_cls.office_parser = providers.Object(components["office_parser"])
    container_cls.pdf_docling_extractor = providers.Object(
        components["docling_pdf_extractor"]
    )
    container_cls.pdf_parser = providers.Object(components["pdf_parser"])
    container_cls.epub_parser = providers.Object(components["epub_parser"])
    container_cls.spreadsheet_parser = providers.Object(components["spreadsheet_parser"])
    container_cls.document_parse_service = providers.Object(
        components["document_parse_service"]
    )

    container_cls.document_file_resolver = providers.Singleton(DocumentTempFileResolver)

    container_cls.document_temp_file_cleanup_service = providers.Singleton(
        DocumentTempFileCleanupService,
    )

    container_cls.document_temp_file_cleanup_scheduler = providers.Singleton(
        DocumentTempFileCleanupScheduler,
        cleanup_service=container_cls.document_temp_file_cleanup_service,
        interval_seconds=DOCUMENT_TEMP_FILE_TTL_SECONDS,
    )


def _register_document_export(container_cls: Any) -> None:
    components = _build_document_export_components()

    container_cls.document_export_html_renderer = providers.Object(components["html_renderer"])
    container_cls.document_export_browser_pool = providers.Object(components["browser_pool"])
    container_cls.document_export_pdf_renderer = providers.Object(components["pdf_renderer"])
    container_cls.document_export_docx_renderer = providers.Object(components["docx_renderer"])
    container_cls.document_export_markdown_renderer = providers.Object(
        components["markdown_renderer"]
    )
    container_cls.document_export_txt_renderer = providers.Object(components["txt_renderer"])
    container_cls.document_export_renderers = providers.Object(components["renderers"])
    container_cls.document_export_atomic_writer = providers.Object(components["atomic_writer"])
    container_cls.document_export_service = providers.Object(components["service"])

    container_cls.document_export_download_resolver = providers.Singleton(
        DocumentDownloadResolver,
        output_root=document_export_output_path(),
    )

    container_cls.document_export_download_service = providers.Singleton(
        DocumentExportDownloadService,
        resolver=container_cls.document_export_download_resolver,
        session_repo=container_cls.session_repo,
    )


def _register_document_convert(container_cls: Any) -> None:
    markdown_converter = MarkdownConverter(
        markitdown=MarkItDown(),
        parse_service=container_cls.document_parse_service(),
        markdown_renderer=container_cls.document_export_markdown_renderer(),
    )

    container_cls.document_convert_markdown_converter = providers.Object(markdown_converter)

    container_cls.document_convert_service = providers.Singleton(
        DocumentConvertService,
        markdown_converter=container_cls.document_convert_markdown_converter,
        export_service=container_cls.document_export_service,
        temp_file_resolver=container_cls.document_file_resolver,
        download_resolver=container_cls.document_export_download_resolver,
    )


def _register_web(container_cls: Any) -> None:
    """注册 web_search、web_fetch、web_crawl 及共用文件交付依赖。"""
    container_cls.web_search_http_client = providers.Resource(_web_search_http_client)

    container_cls.web_search_cache = providers.Singleton(SearchCache)

    container_cls.fourget_searcher = providers.Singleton(
        FourGetSearcher,
        client=container_cls.web_search_http_client,
        base_url=settings.FOURGET_BASE_URL,
        user_agent=tool_settings.WEB_SEARCH_USER_AGENT,
        timeout=tool_settings.FOURGET_TIMEOUT,
        scraper=None,
        max_concurrency=tool_settings.FOURGET_MAX_CONCURRENCY,
    )

    container_cls.serper_searcher = providers.Singleton(
        SerperSearcher,
        client=container_cls.web_search_http_client,
        api_key=tool_settings.SERPER_API_KEY or "",
        base_url=settings.SERPER_BASE_URL,
    )

    container_cls.wikipedia_searcher = providers.Singleton(
        WikipediaSearcher,
        client=container_cls.web_search_http_client,
        base_url=settings.WIKIPEDIA_BASE_URL_TEMPLATE,
        user_agent=tool_settings.WEB_SEARCH_USER_AGENT,
    )

    container_cls.fourget_runner = providers.Singleton(
        FourGetSearchRunner,
        searcher=container_cls.fourget_searcher,
        cache=container_cls.web_search_cache,
        provider_mode=ProviderMode.DEFAULT,
    )

    container_cls.serper_runner = providers.Singleton(
        SerperSearchRunner,
        searcher=container_cls.serper_searcher,
        cache=container_cls.web_search_cache,
        provider_mode=ProviderMode.DEFAULT,
    )

    container_cls.wikipedia_runner = providers.Singleton(
        WikipediaRunner,
        searcher=container_cls.wikipedia_searcher,
    )

    container_cls.custom_provider_runner_factory = providers.Factory(
        CustomProviderRunner,
        client=container_cls.web_search_http_client,
        cache=container_cls.web_search_cache,
    )

    container_cls.web_search_coordinator = providers.Singleton(
        SearchCoordinator,
        fourget_runner=container_cls.fourget_runner,
        serper_runner=container_cls.serper_runner,
        wikipedia_runner=container_cls.wikipedia_runner,
        custom_runner_factory=container_cls.custom_provider_runner_factory.provider,
    )

    container_cls.content_processor = providers.Singleton(
        ContentProcessor,
        min_content_length=tool_settings.FETCH_MIN_CONTENT_LENGTH,
    )

    container_cls.magika_detector = providers.Singleton(MagikaDetector)

    container_cls.filetype_detector = providers.Singleton(
        FileTypeDetector,
        magika_detector=container_cls.magika_detector,
    )

    container_cls.file_handoff_store = providers.Singleton(
        TemporaryFileHandoffStore,
        root_dir=DEFAULT_HANDOFF_ROOT,
    )

    container_cls.static_fetch_http_client = providers.Resource(_static_fetch_http_client)

    container_cls.static_fetcher = providers.Singleton(
        StaticFetcher,
        client=container_cls.static_fetch_http_client,
        max_response_bytes=tool_settings.STATIC_FETCH_MAX_RESPONSE_BYTES,
        filetype_detector=container_cls.filetype_detector,
        processor=container_cls.content_processor,
    )

    container_cls.steel_fetcher_config = providers.Singleton(
        SteelFetcherConfig,
        base_url=settings.STEEL_BASE_URL,
        timeout=tool_settings.STEEL_TIMEOUT,
        delay_ms=tool_settings.STEEL_DELAY_MS,
    )

    container_cls.steel_client = providers.Resource(
        _steel_client,
        config=container_cls.steel_fetcher_config,
    )

    container_cls.steel_fetcher = providers.Singleton(
        SteelFetcher,
        config=container_cls.steel_fetcher_config,
        client=container_cls.steel_client,
        concurrency=tool_settings.STEEL_CONCURRENCY,
        processor=container_cls.content_processor,
    )

    container_cls.local_script_fetcher = providers.Singleton(
        LocalScriptFetcher,
        timeout=tool_settings.LOCAL_SCRIPT_TIMEOUT,
        worker_count=tool_settings.LOCAL_SCRIPT_WORKER_COUNT,
        restart_after=tool_settings.LOCAL_SCRIPT_RESTART_AFTER,
    )

    container_cls.fetch_coordinator = providers.Singleton(
        FetchCoordinator,
        static_fetcher=container_cls.static_fetcher,
        steel_fetcher=container_cls.steel_fetcher,
        local_fetcher=container_cls.local_script_fetcher,
        min_content_length=tool_settings.FETCH_MIN_CONTENT_LENGTH,
        last_resort_min_length=tool_settings.FETCH_LAST_RESORT_MIN_LENGTH,
        cache_ttl_seconds=tool_settings.FETCH_CACHE_TTL_SECONDS,
        cache_max_items=tool_settings.FETCH_CACHE_MAX_ITEMS,
    )

    container_cls.web_crawl_service = providers.Singleton(
        WebCrawlService,
        fetch_coordinator=container_cls.fetch_coordinator,
        file_handoff_store=container_cls.file_handoff_store,
    )


def _register_skill_create(container_cls: Any) -> None:
    container_cls.skill_markdown_renderer = providers.Singleton(SkillMarkdownRenderer)

    container_cls.skill_bundle_artifact_store = providers.Singleton(
        DevSkillBundleArtifactStore,
        output_root=settings.SKILL_ASSETS_CACHE_PATH,
    )

    container_cls.skill_bundle_service = providers.Singleton(
        SkillBundleService,
        artifact_store=container_cls.skill_bundle_artifact_store,
        renderer=container_cls.skill_markdown_renderer,
        rpc_client=container_cls.rpc_client,
    )


def _build_document_parse_components() -> Dict[str, Any]:
    ocr_processor_config = OcrProcessorConfig()
    ocr_processor = OcrProcessor(config=ocr_processor_config)
    ocr_image_adapter = OcrImageAdapter(local_ocr_processor=ocr_processor)

    docling_document_converter = DocumentConverter()
    docling_pdf_extractor = DoclingPdfExtractor(
        do_table_structure=True,
        do_ocr=False,
    )
    markitdown_converter = MarkItDown()
    pp_structure_engine = PPStructure(show_log=False)

    office_primary_parser = OfficePrimaryParser(converter=docling_document_converter)
    office_fallback_parser = OfficeFallbackParser(converter=markitdown_converter)
    office_parser = OfficeParser(
        primary_parser=office_primary_parser,
        fallback_parser=office_fallback_parser,
    )
    pdf_parser = PdfParser(
        classifier=PageClassifier(),
        docling_extractor=docling_pdf_extractor,
        ocr_adapter=ocr_image_adapter,
        table_extractor=TableExtractor(pp_structure_engine=pp_structure_engine),
    )
    epub_parser = EpubParser(converter=markitdown_converter)
    spreadsheet_parser = SpreadsheetParser()
    document_parse_service = DocumentParseService(
        pdf_parser=pdf_parser,
        office_parser=office_parser,
        epub_parser=epub_parser,
        spreadsheet_parser=spreadsheet_parser,
    )
    return {
        "ocr_processor_config": ocr_processor_config,
        "ocr_processor": ocr_processor,
        "ocr_image_adapter": ocr_image_adapter,
        "docling_document_converter": docling_document_converter,
        "docling_pdf_extractor": docling_pdf_extractor,
        "markitdown_converter": markitdown_converter,
        "pp_structure_engine": pp_structure_engine,
        "office_primary_parser": office_primary_parser,
        "office_fallback_parser": office_fallback_parser,
        "office_parser": office_parser,
        "pdf_parser": pdf_parser,
        "epub_parser": epub_parser,
        "spreadsheet_parser": spreadsheet_parser,
        "document_parse_service": document_parse_service,
    }


def _build_document_export_components() -> Dict[str, Any]:
    html_renderer = HtmlRenderer()
    browser_pool = PlaywrightBrowserPool(max_contexts=8)
    pdf_renderer = PdfRenderer(
        html_renderer=html_renderer,
        browser_pool=browser_pool,
    )
    docx_renderer = DocxRenderer(pandoc_bin="pandoc")
    markdown_renderer = MarkdownRenderer()
    txt_renderer = TxtRenderer()
    renderers = {
        renderer.target_format: renderer
        for renderer in [
            markdown_renderer,
            html_renderer,
            pdf_renderer,
            docx_renderer,
            txt_renderer,
        ]
    }
    atomic_writer = AtomicExportWriter()
    service = DocumentExportService(
        output_root=document_export_output_path(),
        markdown_renderer=markdown_renderer,
        renderers=renderers,
        atomic_writer=atomic_writer,
    )
    return {
        "html_renderer": html_renderer,
        "browser_pool": browser_pool,
        "pdf_renderer": pdf_renderer,
        "docx_renderer": docx_renderer,
        "markdown_renderer": markdown_renderer,
        "txt_renderer": txt_renderer,
        "renderers": renderers,
        "atomic_writer": atomic_writer,
        "service": service,
    }


__all__ = ["register_tools"]
