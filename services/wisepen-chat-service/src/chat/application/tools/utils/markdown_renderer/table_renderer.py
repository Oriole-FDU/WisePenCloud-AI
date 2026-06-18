from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from tabulate import tabulate


@dataclass(frozen=True, slots=True)
class TableMarkdownRenderResult:
    markdown: str


class TableMarkdownRenderer:
    """将二维数据或明细行渲染为 GitHub-flavored Markdown 表格。"""

    def __init__(
            self,
            *,
            max_rows: int | None = 200,
            max_columns: int | None = 30,
            empty_cell: str = "",
    ) -> None:
        self._max_rows = max_rows
        self._max_columns = max_columns
        self._empty_cell = empty_cell

    def render(
            self,
            data: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None = None,
            *,
            headers: Sequence[Any] | None = None,
            rows: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None = None,
    ) -> TableMarkdownRenderResult:
        """渲染 Markdown 表格。

        支持 data 自带表头、headers/rows 分开传入、以及 data配合 headers 传入三种模式。
        """
        # 1. 归一化输入形态
        normalized_headers, normalized_rows = self._normalize_input(
            data=data, headers=headers, rows=rows,
        )

        source_row_count = len(normalized_rows)
        source_column_count = max(
            len(normalized_headers),
            max((len(r) for r in normalized_rows), default=0),
        )

        # 空表防御
        if source_column_count == 0:
            return TableMarkdownRenderResult(markdown="")

        # 2. 计算安全截断维度
        effective_columns = self._clamp(source_column_count, self._max_columns)
        effective_rows = self._clamp(source_row_count, self._max_rows)

        # 3. 规整并对齐表头宽度
        display_headers = list(normalized_headers[:effective_columns])
        if len(display_headers) < effective_columns:
            display_headers += [
                f"Column {i + 1}"
                for i in range(len(display_headers), effective_columns)
            ]

        # 4. 数据截断与单元格字符清洗
        display_rows = [
            [self._sanitize_cell(cell) for cell in row[:effective_columns]]
            for row in normalized_rows[:effective_rows]
        ]

        # 5. 驱动排版引擎生成 Markdown 文本
        markdown = tabulate(
            display_rows,
            headers=[self._sanitize_cell(h) for h in display_headers],
            tablefmt="github",
            missingval=self._empty_cell,
        )

        return TableMarkdownRenderResult(markdown=markdown)

    @staticmethod
    def _clamp(value: int, limit: int | None) -> int:
        """数值边界收拢限制。"""
        return value if limit is None else max(0, min(value, limit))

    def _sanitize_cell(self, value: Any) -> str:
        """清洗单元格：替换换行符并转义 Markdown 敏感符号。"""
        text = self._empty_cell if value is None else str(value)

        # 统一换行符为 HTML <br>，并转义反斜杠与竖线
        text = text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
        return text.replace("\\", "\\\\").replace("|", "\\|")

    @staticmethod
    def _normalize_input(
            *,
            data: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None,
            headers: Sequence[Any] | None,
            rows: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None,
    ) -> tuple[list[Any], list[list[Any]]]:
        """将多种兼容调用形态统一路由转换为 (headers, rows)。"""
        if rows is not None:
            return list(headers or []), [list(row) for row in rows]

        table_rows = [list(row) for row in (data or [])]
        if headers is not None:
            return list(headers), table_rows

        if not table_rows:
            return [], []

        return list(table_rows[0]), table_rows[1:]