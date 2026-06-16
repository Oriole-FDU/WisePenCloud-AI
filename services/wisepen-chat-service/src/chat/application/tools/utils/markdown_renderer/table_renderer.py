from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from tabulate import tabulate


# ---------------------------------------------------------------------------
# 数据类：渲染结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TableMarkdownRenderResult:
    markdown: str


# ---------------------------------------------------------------------------
# 渲染器
# ---------------------------------------------------------------------------

class TableMarkdownRenderer:
    """将表格数据渲染为 GitHub-flavored Markdown 表格。

    支持三种输入约定，调用方需要按数据来源选择其中一种：

    1. 二维数组自带表头：
       `render([["姓名", "分数"], ["Alice", 98]])`
       第一行会被解释为表头，其余行解释为数据行。适合 OCR/Excel 已经产出
       "完整表格矩阵" 的场景。

    2. 表头和数据行分开传入：
       `render(headers=["姓名", "分数"], rows=[["Alice", 98]])`
       `headers` 只作为表头，`rows` 全部作为数据行。适合调用方已经明确区分
       schema/header 和 body rows 的场景。

    3. 二维数组是纯数据，表头单独传入：
       `render(data=[["Alice", 98]], headers=["姓名", "分数"])`
       `data` 不会再取第一行当表头。适合 pandas/openpyxl 这类上游已经
       单独提供列名或需要人工生成列名的场景。

    当同时传入 `rows` 和 `data` 时，以 `rows` 为准；这是为了让显式的
    headers/rows 调用不受兼容参数 `data` 的影响。

    底层使用 `tabulate(tablefmt="github")` 做 Markdown 表格排版；本类负责：
    - 输入约定归一化
    - Markdown 表格单元格安全转义
    - 行列截断
    - 渲染规模和截断元信息收集
    """

    def __init__(
        self,
        *,
        max_rows: int | None = 200,     # None 表示不限制行数
        max_columns: int | None = 30,   # None 表示不限制列数
        empty_cell: str = "",           # None 值的替代文本
    ) -> None:
        self._max_rows = max_rows
        self._max_columns = max_columns
        self._empty_cell = empty_cell

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def render(
        self,
        data: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None = None,
        *,
        headers: Sequence[Any] | None = None,
        rows: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None = None,
    ) -> TableMarkdownRenderResult:
        """渲染 Markdown 表格并返回 Markdown 文本。

        参数说明：
        - `data`：兼容两种模式。单独传入时，第一行是表头；配合 `headers`
          传入时，全部是数据行。
        - `headers`：显式表头。可与 `rows` 或 `data` 配合使用。
        - `rows`：显式数据行。传入后优先级最高，`data` 会被忽略。
        """
        # 先把三种公开调用形式归一为统一内部形态：
        # normalized_headers: list[Any]
        # normalized_rows: list[list[Any]]
        # 后续截断、补列、转义都只处理这一种结构，避免各调用路径行为不一致。
        normalized_headers, normalized_rows = self._normalize_input(
            data=data, headers=headers, rows=rows,
        )

        source_row_count = len(normalized_rows)
        # 原始列数 = max(表头宽度, 所有行的最大宽度)
        # 注意：max() 第一个参数保证了列表非空，`or [0]` 是死代码，这里改用 default=
        source_column_count = max(
            len(normalized_headers),
            max((len(r) for r in normalized_rows), default=0),
        )

        # 空表短路返回
        if source_column_count == 0:
            return TableMarkdownRenderResult(markdown="")

        # 计算截断后的有效维度
        effective_columns = self._clamp(source_column_count, self._max_columns)
        effective_rows = self._clamp(source_row_count, self._max_rows)

        # 截断/补齐表头到 effective_columns 个
        display_headers = list(normalized_headers[:effective_columns])
        if len(display_headers) < effective_columns:
            # 超出表头范围的列自动命名为 "Column N"
            display_headers += [
                f"Column {i + 1}"
                for i in range(len(display_headers), effective_columns)
            ]

        # 截断数据行（列方向 + 行方向），并对单元格做 Markdown 表格安全处理。
        # 这里提前转为字符串，是为了让 tabulate 只负责排版，不负责语义转换。
        display_rows = [
            [self._sanitize_cell(cell) for cell in row[:effective_columns]]
            for row in normalized_rows[:effective_rows]
        ]

        # tabulate "github" 格式：输出 GitHub-flavored Markdown 表格
        # `|` 和 `\` 已在 _sanitize_cell 中处理；missingval 处理行内缺失列。
        markdown = tabulate(
            display_rows,
            headers=[self._sanitize_cell(h) for h in display_headers],
            tablefmt="github",
            missingval=self._empty_cell,
        )

        return TableMarkdownRenderResult(markdown=markdown)

    # ------------------------------------------------------------------
    # 私有工具
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(value: int, limit: int | None) -> int:
        """将 value 限制在 [0, limit]；limit 为 None 时不限制。"""
        return value if limit is None else max(0, min(value, limit))

    def _sanitize_cell(self, value: Any) -> str:
        """单元格值 → 安全字符串。

        - None 替换为 empty_cell 占位符
        - 换行符替换为 <br>（Markdown 表格不支持真换行）
        - 反斜杠先转义，再转义竖线，避免 `|` 被 Markdown 误识别为列分隔符
        """
        text = self._empty_cell if value is None else str(value)
        # \r\n / \n / \r 统一转为 HTML <br>
        text = text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
        return text.replace("\\", "\\\\").replace("|", "\\|")

    @staticmethod
    def _normalize_input(
        *,
        data: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None,
        headers: Sequence[Any] | None,
        rows: Sequence[Sequence[Any]] | Iterable[Sequence[Any]] | None,
    ) -> tuple[list[Any], list[list[Any]]]:
        """统一三种调用约定，返回 (headers, rows) 的规范化形式。

        调用形式优先级：
        1. `rows=` 显式传入：
           - `headers=` 作为表头，可省略
           - `data` 被忽略
           - 用于上游已经拆分 headers/body rows 的场景

        2. `data=` + `headers=`：
           - `headers` 作为表头
           - `data` 全部作为数据行
           - 用于 data 本身不含表头的场景

        3. 仅 `data=`：
           - `data[0]` 作为表头
           - `data[1:]` 作为数据行
           - 用于二维数组天然包含完整表格的场景
        """
        if rows is not None:
            # 显式区分了表头和行，直接使用
            return list(headers or []), [list(row) for row in rows]

        table_rows = [list(row) for row in (data or [])]
        if headers is not None:
            # data 是纯数据，headers 单独给出
            return list(headers), table_rows

        # 退化模式：第一行当表头
        if not table_rows:
            return [], []
        return list(table_rows[0]), table_rows[1:]
