"""缓存单测不连接 Nacos；在真实配置模块边界提供最小设置。"""

import sys
from types import ModuleType, SimpleNamespace

_settings_module = "chat.core.config.app_settings"
_previous = None


def pytest_configure():
    global _previous
    _previous = sys.modules.get(_settings_module)
    module = ModuleType(_settings_module)
    module.settings = SimpleNamespace(
        TOOL_CONTENT_READ_WINDOW_CHAR_BUDGET=4000,
    )
    sys.modules[_settings_module] = module


def pytest_unconfigure():
    if _previous is None:
        sys.modules.pop(_settings_module, None)
    else:
        sys.modules[_settings_module] = _previous
