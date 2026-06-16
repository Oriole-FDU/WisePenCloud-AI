from __future__ import annotations

from chat.application.tools.document_tools.document_parse.errors import (
    DocumentParseFailedError,
)
from chat.application.tools.document_tools.document_parse.models import DocumentParseRequest, DocumentParseResult
from chat.application.tools.document_tools.document_parse.planner import DocumentParsePlanner


class DocumentParseService:
    """文档解析编排入口。

    Service 只负责执行 ParsePlan 和汇总失败，不关心具体格式解析细节。
    """

    def __init__(
        self,
        *,
        planner: DocumentParsePlanner | None = None,
    ) -> None:
        self._planner = planner or DocumentParsePlanner()

    async def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        """按候选计划顺序解析文档。

        Args:
            request: 文档解析请求。

        Returns:
            第一个成功候选产出的解析结果。

        Raises:
            DocumentParseFailedError: 所有候选解析器都失败时抛出。
        """
        plan = self._planner.plan(request)
        last_error: BaseException | None = None
        for candidate in plan.candidates:
            try:
                return await candidate.parser.parse(request)
            except Exception as e:
                # 候选失败不立即中断，交给后续候选或兜底解析器继续尝试。
                last_error = e

        raise DocumentParseFailedError(
            "Every document parser candidate failed.",
            parser_name=(
                "component=document_parse_service;stage=execute_plan;"
                f"suffix={request.suffix or '<none>'};mime={request.mime_type or '<none>'};"
                f"roles={','.join(candidate.role for candidate in plan.candidates)}"
            ),
            cause=last_error,
        )
