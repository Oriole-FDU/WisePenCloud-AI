from chat.application.tools.search_tools import web_search
from chat.application.tools.search_tools.web_search import tools


def test_web_search_package_reexports_current_provider_tools() -> None:
    exported_names = (
        "AnySearchSearchTool",
        "BaiduQianfanSearchTool",
        "ExaSearchTool",
        "FirecrawlSearchTool",
        "PlatformSearchTool",
        "TavilySearchTool",
        "TinyFishSearchTool",
    )

    for name in exported_names:
        assert getattr(web_search, name) is getattr(tools, name)
