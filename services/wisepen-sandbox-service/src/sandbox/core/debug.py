"""
Shared debug logger for all sandbox modules.

Usage:
    from sandbox.core.debug import debug
    _dbg = debug("[SANDBOX][module_name]")
    _dbg("event_name", key1=val1, key2=val2)
"""
from __future__ import annotations

import json
import os
from typing import Any

_DEBUG = (os.getenv("SANDBOX_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def debug(prefix: str):
    """Return a logger function that prints debug messages with the given prefix.

    Args:
        prefix: Tag like "[SANDBOX][queue]", "[SANDBOX][http]", etc.

    Returns:
        Callable(event: str, **fields) that prints JSON-key=value lines.
    """
    def _log(event: str, **fields: Any) -> None:
        if not _DEBUG:
            return
        try:
            payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            payload = str(fields)
        print(f"{prefix} {event} | {payload}")
    return _log
