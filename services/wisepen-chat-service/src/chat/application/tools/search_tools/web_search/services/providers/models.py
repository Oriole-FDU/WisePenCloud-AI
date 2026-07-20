from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderSearchHttpRequest:
    """Provider HTTP 请求描述。"""

    method: str
    path: str
    params: dict[str, object] | None = None
    json: dict[str, object] | None = None
