from __future__ import annotations

from typing import Any

import sympy as sp


def latex_or_none(value: Any) -> str | None:
    """尽力生成 LaTeX；第三方对象不支持时返回 None。"""
    if value is None:
        return None
    try:
        return str(sp.latex(value))
    except Exception:
        return None