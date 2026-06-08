import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.figure import Figure

from chat.application.tools.chart.services.function_plot.models import (
    FunctionPlotFeature,
    FunctionPlotRequest,
    GridSample,
    LineSeries,
)
from chat.application.tools.chart.services.function_plot.styles import (
    FIGURE_SIZES,
    SURFACE_FIGURE_SIZES,
    get_style_profile,
)


class FunctionPlotRenderer:
    """Matplotlib 函数图渲染器。

    renderer 只负责把采样结果画成 Figure；不做表达式解析、不做入参校验、
    不负责导出格式。
    """

    def render_line(
        self,
        request: FunctionPlotRequest,
        series_items: list[LineSeries],
        features: list[FunctionPlotFeature],
    ) -> Figure:
        """渲染一元函数曲线图。

        Args:
            request: 可信绘图请求。
            series_items: 一条或多条曲线采样结果。
            features: 可选零点/极值标注。

        Returns:
            Matplotlib Figure，由 service 负责关闭。
        """
        with plt.rc_context(get_style_profile(request.style_profile)):
            figure, axis = plt.subplots(
                figsize=self._figure_size(request.style_profile),
                layout="constrained",
            )
            for series in series_items:
                axis.plot(series.x, series.y, label=f"${series.expression.latex}$")
            axis.set_xlabel(request.variables[0])
            axis.set_ylabel("y")
            axis.set_title(request.title or "Function Plot")
            if len(series_items) > 1:
                axis.legend()
            for feature in features:
                if feature.x is None or feature.y is None:
                    continue
                axis.scatter([feature.x], [feature.y], s=28, zorder=4)
                axis.annotate(feature.kind, (feature.x, feature.y), textcoords="offset points", xytext=(6, 6))
            return figure

    def render_surface(self, request: FunctionPlotRequest, grid: GridSample) -> Figure:
        """渲染二元函数三维曲面图。"""
        with plt.rc_context(get_style_profile(request.style_profile)):
            figure = plt.figure(
                figsize=self._figure_size(request.style_profile, surface=True),
                layout="constrained",
            )
            axis = figure.add_subplot(111, projection="3d")
            surface = axis.plot_surface(
                grid.x,
                grid.y,
                grid.z,
                cmap="viridis",
                linewidth=0,
                antialiased=True,
            )
            axis.set_xlabel(request.variables[0])
            axis.set_ylabel(request.variables[1])
            axis.set_zlabel("z")
            axis.set_title(request.title or "3D Surface Plot")
            figure.colorbar(surface, ax=axis, shrink=0.65, pad=0.08)
            return figure

    def render_contour(self, request: FunctionPlotRequest, grid: GridSample) -> Figure:
        """渲染二元函数等高线图。"""
        with plt.rc_context(get_style_profile(request.style_profile)):
            figure, axis = plt.subplots(
                figsize=self._figure_size(request.style_profile),
                layout="constrained",
            )
            contour = axis.contourf(grid.x, grid.y, grid.z, levels=32, cmap="viridis")
            axis.set_xlabel(request.variables[0])
            axis.set_ylabel(request.variables[1])
            axis.set_title(request.title or "Contour Plot")
            figure.colorbar(contour, ax=axis)
            return figure

    def _figure_size(self, style_profile: str, *, surface: bool = False) -> tuple[float, float]:
        """返回与语义样式绑定的默认画布尺寸。"""
        if surface:
            return SURFACE_FIGURE_SIZES[style_profile]
        return FIGURE_SIZES[style_profile]
