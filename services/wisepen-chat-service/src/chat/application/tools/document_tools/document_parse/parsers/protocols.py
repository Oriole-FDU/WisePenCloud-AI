from __future__ import annotations

from typing import Protocol

from chat.application.tools.document_tools.document_parse.models import DocumentParseRequest, DocumentParseResult


class Parser(Protocol):
    async def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        """解析当前 parser 支持的输入。

        Args:
            request: 文档解析请求。

        Returns:
            统一的 Markdown 解析结果。
        """
