
import uuid

from matplotlib import pyplot as plt

from chat.application.tools.chart.services.function_plot.analyzer import (
    FunctionFeatureAnalyzer,
)
from chat.application.tools.chart.services.function_plot.exporter import (
    FunctionPlotExporter,
)
from chat.application.tools.chart.services.function_plot.models import (
    FunctionPlotRequest,
    FunctionPlotResult,
)
from chat.application.tools.chart.services.function_plot.parser import (
    FunctionExpressionParser,
)
from chat.application.tools.chart.services.function_plot.renderer import (
    FunctionPlotRenderer,
)
from chat.application.tools.chart.services.function_plot.sampler import FunctionSampler


class FunctionPlotService:
    """函数绘图业务编排服务。

    该 service 接收 tool 层构造好的可信 `FunctionPlotRequest`，只负责
    parse -> sample -> analyze -> render -> export 的业务链路。不要在这里
    处理 LLM 原始 kwargs 或重复 JSON schema 能表达的参数校验。

    Args:
        parser: 数学表达式解析器。
        sampler: NumPy 采样器。
        analyzer: 零点/极值检测器。
        renderer: Matplotlib 渲染器。
        exporter: SVG/PNG 导出器。
    """

    def __init__(
        self,
        *,
        parser: FunctionExpressionParser,
        sampler: FunctionSampler,
        analyzer: FunctionFeatureAnalyzer,
        renderer: FunctionPlotRenderer,
        exporter: FunctionPlotExporter,
    ) -> None:
        self._parser = parser
        self._sampler = sampler
        self._analyzer = analyzer
        self._renderer = renderer
        self._exporter = exporter

    def render(self, request: FunctionPlotRequest) -> FunctionPlotResult:
        """渲染函数图并导出 artifact。

        Args:
            request: tool 层构造的可信绘图请求。

        Returns:
            包含 LaTeX 表达式、SVG/PNG artifact、可选特征点和 warning 的结果。

        Raises:
            FunctionPlotError: 表达式解析、采样或渲染过程中发生可预期失败。
        """
        parsed = [self._parser.parse(expression, request.variables) for expression in request.expressions]
        warnings: list[str] = []
        features = []
        figure = None

        try:
            if request.plot_kind == "line_2d":
                # 一元函数支持多表达式同图；每条曲线独立采样，避免单条失败污染其他表达式。
                series_items = [
                    self._sampler.sample_line(
                        expression,
                        variable=request.variables[0],
                        x_range=request.x_range,
                        samples=request.samples,
                    )
                    for expression in parsed
                ]
                for series in series_items:
                    warnings.extend(series.warnings)
                if len(series_items) == 1:
                    # 零点/极值检测只对单曲线有明确语义，多曲线时降级为 warning。
                    features = self._analyzer.analyze(
                        series_items[0],
                        variable=request.variables[0],
                        detect_roots=request.detect_roots,
                        detect_extrema=request.detect_extrema,
                    )
                elif request.detect_roots or request.detect_extrema:
                    warnings.append("feature detection is only applied to a single line_2d expression.")
                figure = self._renderer.render_line(request, series_items, features)
            else:
                if "svg" in request.output_formats and request.plot_kind == "surface_3d":
                    warnings.append("3D SVG output may be large; png is recommended for surface_3d.")
                # 二元图共用网格采样，后续由 renderer 决定曲面或等高线呈现。
                grid = self._sampler.sample_grid(
                    parsed[0],
                    variables=request.variables,
                    x_range=request.x_range,
                    y_range=request.y_range or (-10.0, 10.0),
                    samples=request.samples,
                )
                warnings.extend(grid.warnings)
                if request.detect_roots or request.detect_extrema:
                    warnings.append("feature detection is only supported for line_2d.")
                figure = (
                    self._renderer.render_surface(request, grid)
                    if request.plot_kind == "surface_3d"
                    else self._renderer.render_contour(request, grid)
                )
            artifacts = self._exporter.export(figure, request.output_formats)
        finally:
            if figure is not None:
                # Matplotlib figure 持有全局资源；导出后必须关闭，避免服务进程内存增长。
                plt.close(figure)

        return FunctionPlotResult(
            plot_id=f"fplot_{uuid.uuid4().hex[:12]}",
            plot_kind=request.plot_kind,
            latex_expressions=[item.latex for item in parsed],
            artifacts=artifacts,
            features=features,
            warnings=warnings,
            metadata={
                "variables": request.variables,
                "x_range": list(request.x_range),
                "y_range": list(request.y_range) if request.y_range else None,
                "samples": request.samples,
                "style_profile": request.style_profile,
            },
        )
