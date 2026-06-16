from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # OCR 行为
    PADDLE_OCR_TIMEOUT_SECONDS: float = 60.0
    PADDLE_OCR_RETRIES: int = 2

    # Web Search 行为
    WEB_SEARCH_TIMEOUT_SECONDS: float = 15.0

    # Web Fetch 行为
    WEB_FETCH_TIMEOUT_SECONDS: float = 15.0
    WEB_FETCH_MAX_RESPONSE_BYTES: int = 52_428_800  # 50 MiB
    WEB_FETCH_MIN_TEXT_LENGTH: int = 200
    WEB_FETCH_BATCH_CONCURRENCY: int = 5


tool_settings = ToolSettings()
