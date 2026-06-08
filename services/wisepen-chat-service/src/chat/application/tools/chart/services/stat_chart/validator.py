import pandas as pd

from chat.application.tools.chart.services.stat_chart.errors import StatChartError
from chat.application.tools.chart.services.stat_chart.models import (
    StatChartMapping,
    StatChartOptions,
    StatChartRequest,
)

CHART_KINDS = {
    "scatter",
    "line",
    "histogram",
    "kde",
    "ecdf",
    "bar",
    "count",
    "box",
    "violin",
    "boxen",
    "strip",
    "swarm",
    "point",
    "regression",
    "residual",
    "heatmap",
    "correlation_heatmap",
    "pairplot",
}
MAX_FACETS = 12
MAX_HEATMAP_DIMENSION = 80
MAX_PAIRPLOT_ROWS = 1000
MAX_PAIRPLOT_NUMERIC_COLUMNS = 6


class StatChartSpecValidator:
    """验证图表规格和 DataFrame 是否匹配。

    mapping 字段是列名边界，必须在这里确认存在且不是表达式。service 不做
    renderer 前置保护，renderer 可以假设 spec 已经通过本验证器。
    """

    def validate(self, request: StatChartRequest, frame: pd.DataFrame) -> list[str]:
        """验证统计图规格。

        Args:
            request: tool 层构造的可信请求。
            frame: DataFrameBuilder 产出的表格。

        Returns:
            非阻断 warning 列表。

        Raises:
            StatChartError: 字段缺失、列不存在、规模超限或图型不支持。
        """
        if request.chart_kind not in CHART_KINDS:
            raise StatChartError(f"unsupported chart_kind: {request.chart_kind}")

        self._validate_mapping_columns(request.mapping, frame)
        self._validate_required_fields(request)
        self._validate_options(request.options)
        self._validate_facets(request.mapping, frame)

        if request.chart_kind == "heatmap":
            self._validate_heatmap(request.mapping, frame)
        if request.chart_kind in {"correlation_heatmap", "pairplot"}:
            self.numeric_columns(frame)
        if request.chart_kind == "pairplot":
            self._validate_pairplot(frame)
        return []

    def numeric_columns(self, frame: pd.DataFrame) -> list[str]:
        """返回可用于相关矩阵或 pairplot 的数值列。"""
        columns = list(frame.select_dtypes(include="number").columns)
        if len(columns) < 2:
            raise StatChartError("correlation_heatmap and pairplot require at least two numeric columns.")
        return [str(column) for column in columns]

    def _validate_mapping_columns(self, mapping: StatChartMapping, frame: pd.DataFrame) -> None:
        columns = set(frame.columns)
        for field_name, column in mapping.selected_columns().items():
            if column not in columns:
                raise StatChartError(f"mapping.{field_name} column not found: {column}")
            if any(token in column for token in ("(", ")", "[", "]", "{", "}", "+", "-", "*", "/", "=")):
                raise StatChartError(f"mapping.{field_name} must be a plain column name, not an expression.")

    def _validate_required_fields(self, request: StatChartRequest) -> None:
        mapping = request.mapping
        required_by_kind = {
            "scatter": ("x", "y"),
            "line": ("x", "y"),
            "histogram": ("x",),
            "ecdf": ("x",),
            "bar": ("x", "y"),
            "count": ("x",),
            "box": ("x", "y"),
            "violin": ("x", "y"),
            "boxen": ("x", "y"),
            "strip": ("x", "y"),
            "swarm": ("x", "y"),
            "point": ("x", "y"),
            "regression": ("x", "y"),
            "residual": ("x", "y"),
            "heatmap": ("x", "y", "value"),
        }
        if request.chart_kind == "kde" and not mapping.x:
            raise StatChartError("kde requires mapping.x.")
        for field_name in required_by_kind.get(request.chart_kind, ()):
            if getattr(mapping, field_name) is None:
                raise StatChartError(f"{request.chart_kind} requires mapping.{field_name}.")

    def _validate_options(self, options: StatChartOptions) -> None:
        # 这些范围虽然 schema 已写，但直接调用 service 的测试/内部调用也应在真实风险边界前兜住资源参数。
        if options.bins is not None and not 5 <= options.bins <= 100:
            raise StatChartError("options.bins must be between 5 and 100.")
        if not 50 <= options.confidence_level <= 99:
            raise StatChartError("options.confidence_level must be between 50 and 99.")
        if not 1 <= options.regression_order <= 3:
            raise StatChartError("options.regression_order must be between 1 and 3.")

    def _validate_facets(self, mapping: StatChartMapping, frame: pd.DataFrame) -> None:
        if not mapping.row and not mapping.col:
            return
        row_count = frame[mapping.row].nunique(dropna=True) if mapping.row else 1
        col_count = frame[mapping.col].nunique(dropna=True) if mapping.col else 1
        if int(row_count) * int(col_count) > MAX_FACETS:
            raise StatChartError(f"facet row/col combinations must be less than or equal to {MAX_FACETS}.")

    def _validate_heatmap(self, mapping: StatChartMapping, frame: pd.DataFrame) -> None:
        pivot_shape = frame.pivot_table(
            index=mapping.y,
            columns=mapping.x,
            values=mapping.value,
            aggfunc="mean",
        ).shape
        if pivot_shape[0] > MAX_HEATMAP_DIMENSION or pivot_shape[1] > MAX_HEATMAP_DIMENSION:
            raise StatChartError("heatmap pivot table must be at most 80 x 80.")

    def _validate_pairplot(self, frame: pd.DataFrame) -> None:
        numeric_columns = self.numeric_columns(frame)
        if len(numeric_columns) > MAX_PAIRPLOT_NUMERIC_COLUMNS:
            raise StatChartError("pairplot supports at most 6 numeric columns.")
        if len(frame) > MAX_PAIRPLOT_ROWS:
            raise StatChartError("pairplot supports at most 1000 rows.")
