from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # OCR 行为
    PADDLE_OCR_TIMEOUT_SECONDS: float = 300.0  # 异步任务可能需要较长时间
    PADDLE_OCR_POLL_INTERVAL_SECONDS: float = 5.0
    PADDLE_OCR_MAX_POLL_ATTEMPTS: int = 60  # 最多轮询 60 次（5 分钟）

    # Web Search 行为
    WEB_SEARCH_TIMEOUT_SECONDS: float = 15.0

    # Web Fetch 行为
    WEB_FETCH_TIMEOUT_SECONDS: float = 15.0
    WEB_FETCH_MAX_RESPONSE_BYTES: int = 52_428_800  # 50 MiB
    WEB_FETCH_MIN_TEXT_LENGTH: int = 200
    WEB_FETCH_BATCH_CONCURRENCY: int = 5


tool_settings = ToolSettings()
