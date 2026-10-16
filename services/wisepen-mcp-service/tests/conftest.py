"""在应用配置边界隔离 Nacos，测试仍使用真实工具导入与注册路径。"""

import sys
from types import ModuleType, SimpleNamespace

_SETTINGS_MODULE = "wisepen_mcp.core.config.app_settings"
_previous = None


def pytest_configure():
    global _previous
    _previous = sys.modules.get(_SETTINGS_MODULE)
    module = ModuleType(_SETTINGS_MODULE)
    module.settings = SimpleNamespace(
        WEB_SEARCH_API_KEY="test-platform-key",
        WEB_SEARCH_DEEPSEEK_BASE_URL="https://api.deepseek.com",
        WEB_SEARCH_HTTP_TIMEOUT_SECONDS=15.0,
        WEB_SEARCH_TINYFISH_BASE_URL="https://api.search.tinyfish.ai",
        WEB_SEARCH_BAIDU_QIANFAN_BASE_URL="https://qianfan.baidubce.com",
        WEB_SEARCH_ANYSEARCH_BASE_URL="https://api.anysearch.com",
    )
    sys.modules[_SETTINGS_MODULE] = module


def pytest_unconfigure():
    if _previous is None:
        sys.modules.pop(_SETTINGS_MODULE, None)
    else:
        sys.modules[_SETTINGS_MODULE] = _previous
