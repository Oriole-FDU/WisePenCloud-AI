import uuid

from matplotlib import pyplot as plt

from chat.application.tools.chart.services.stat_chart.dataframe import DataFrameBuilder
from chat.application.tools.chart.services.stat_chart.exporter import StatChartExporter
from chat.application.tools.chart.services.stat_chart.models import (
    StatChartRequest,
    StatChartResult,
)
from chat.application.tools.chart.services.stat_chart.renderer import StatChartRenderer
from chat.application.tools.chart.services.stat_chart.validator import StatChartSpecValidator


class StatChartService:
    """统计图业务编排服务。

    service 接收 tool 层构造的可信 `StatChartRequest`，只负责
    build_dataframe -> validate_spec -> render -> export 的主链路。
    """

    def __init__(
        self,
        *,
        dataframe_builder: DataFrameBuilder,
        validator: StatChartSpecValidator,
        renderer: StatChartRenderer,
        exporter: StatChartExporter,
    ) -> None:
        self._dataframe_builder = dataframe_builder
        self._validator = validator
        self._renderer = renderer
        self._exporter = exporter

    def render(self, request: StatChartRequest) -> StatChartResult:
        """渲染统计图并导出 artifact。

        Args:
            request: tool 层收敛后的可信统计图请求。

        Returns:
            包含 SVG/PNG artifact、列信息、行数和 warning 的结果。
        """
        frame, warnings = self._dataframe_builder.build(request.data)
        warnings.extend(self._validator.validate(request, frame))
        figure = None
        try:
            figure = self._renderer.render(request, frame)
            artifacts = self._exporter.export(figure, request.output_formats)
        finally:
            if figure is not None:
                plt.close(figure)

        return StatChartResult(
            chart_id=f"schart_{uuid.uuid4().hex[:12]}",
            chart_kind=request.chart_kind,
            artifacts=artifacts,
            columns=[str(column) for column in frame.columns],
            row_count=len(frame),
            warnings=warnings,
            metadata={
                "style_profile": request.style_profile,
                "output_formats": request.output_formats,
            },
        )
