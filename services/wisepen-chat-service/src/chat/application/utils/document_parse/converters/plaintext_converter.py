from __future__ import annotations

import asyncio
from pathlib import Path

from .utils import decode_text


class PlaintextConverter:
    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        raw = await asyncio.to_thread(file_path.read_bytes)
        return decode_text(raw, file_name=file_name)
