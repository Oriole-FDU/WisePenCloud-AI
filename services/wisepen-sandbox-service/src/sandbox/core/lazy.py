"""
Lazy-loading __getattr__ factory for package __init__.py files.

Usage in any sandbox/xxx/__init__.py:

    from sandbox.core.lazy import make_getattr

    __all__ = ["Foo", "Bar"]
    _LAZY = {
        "Foo": "sandbox.xxx.module_a",
        "Bar": "sandbox.xxx.module_b",
    }
    __getattr__ = make_getattr(_LAZY)
"""
from __future__ import annotations

import importlib
from typing import Any


def make_getattr(mapping: dict[str, str]):
    """Generate a __getattr__ that lazy-loads names from their source modules.

    Args:
        mapping: {exported_name: "full.dotted.module.path"}

    Returns:
        Callable suitable for assignment to __getattr__ in a package __init__.py
    """
    def __getattr__(name: str) -> Any:
        mod_path = mapping.get(name)
        if mod_path is None:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            )
        return getattr(importlib.import_module(mod_path), name)
    return __getattr__
