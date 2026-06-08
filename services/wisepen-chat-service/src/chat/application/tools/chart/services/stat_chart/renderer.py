import matplotlib

matplotlib.use("Agg")

import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt
from matplotlib.figure import Figure

from chat.application.tools.chart.services.stat_chart.models import (
    StatChartMapping,
    StatChartRequest,
)
from chat.application.tools.chart.services.stat_chart.styles import (
    PALETTE_BY_ROLE,
    figure_size,
    get_style_profile,
)


class StatChartRenderer:
    """Seaborn/Matplotlib 统计图渲染器。

    renderer 假设 request 已由 validator 验证，只按受控 chart_kind 调用固定
    seaborn API，不接受用户传入 seaborn/matplotlib kwargs。
    """

    def render(self, request: StatChartRequest, frame: pd.DataFrame) -> Figure:
        """渲染统计图。

        Args:
            request: tool 层构造并通过 validator 的绘图请求。
            frame: DataFrameBuilder 产出的数据表。

        Returns:
            Matplotlib Figure，由 service 负责关闭。
        """
        with plt.rc_context(get_style_profile(request.style_profile)):
            if request.chart_kind == "pairplot":
                return self._render_pairplot(request, frame)
            square = request.chart_kind in {"heatmap", "correlation_heatmap"}
            figure, axis = plt.subplots(
                figsize=figure_size(request.style_profile, square=square),
                layout="constrained",
            )
            self._draw_axes_chart(request, frame, axis)
            self._apply_axes_postprocess(request, axis)
            return figure

    def _draw_axes_chart(self, request: StatChartRequest, frame: pd.DataFrame, axis: plt.Axes) -> None:
        """按 chart_kind 调用固定 seaborn axes-level API。"""
        mapping = request.mapping
        options = request.options
        if request.chart_kind == "scatter":
            sns.scatterplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, style=mapping.style, size=mapping.size, ax=axis)
        elif request.chart_kind == "line":
            sns.lineplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, style=mapping.style, ax=axis)
        elif request.chart_kind == "histogram":
            sns.histplot(data=frame, x=mapping.x, hue=mapping.hue, bins=options.bins, kde=options.kde, ax=axis)
        elif request.chart_kind == "kde":
            sns.kdeplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, ax=axis)
        elif request.chart_kind == "ecdf":
            sns.ecdfplot(data=frame, x=mapping.x, hue=mapping.hue, ax=axis)
        elif request.chart_kind == "bar":
            sns.barplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, estimator=options.estimator, errorbar=options.errorbar, ax=axis)
            self._add_data_labels_if_needed(request, axis)
        elif request.chart_kind == "count":
            sns.countplot(data=frame, x=mapping.x, hue=mapping.hue, ax=axis)
            self._add_data_labels_if_needed(request, axis)
        elif request.chart_kind == "box":
            sns.boxplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, ax=axis)
        elif request.chart_kind == "violin":
            sns.violinplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, ax=axis)
        elif request.chart_kind == "boxen":
            sns.boxenplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, ax=axis)
        elif request.chart_kind == "strip":
            sns.stripplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, ax=axis)
        elif request.chart_kind == "swarm":
            sns.swarmplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, ax=axis)
        elif request.chart_kind == "point":
            sns.pointplot(data=frame, x=mapping.x, y=mapping.y, hue=mapping.hue, estimator=options.estimator, errorbar=options.errorbar, ax=axis)
        elif request.chart_kind == "regression":
            sns.regplot(data=frame, x=mapping.x, y=mapping.y, order=options.regression_order, robust=options.robust, lowess=options.lowess, ax=axis)
        elif request.chart_kind == "residual":
            sns.residplot(data=frame, x=mapping.x, y=mapping.y, ax=axis)
        elif request.chart_kind == "heatmap":
            matrix = frame.pivot_table(index=mapping.y, columns=mapping.x, values=mapping.value, aggfunc="mean")
            sns.heatmap(matrix, annot=options.annot, cmap=PALETTE_BY_ROLE["sequential"], ax=axis)
        elif request.chart_kind == "correlation_heatmap":
            numeric_frame = frame.select_dtypes(include="number")
            cmap = PALETTE_BY_ROLE["diverging"] if options.center_zero else PALETTE_BY_ROLE["sequential"]
            center = 0 if options.center_zero else None
            sns.heatmap(numeric_frame.corr(), annot=options.annot, cmap=cmap, center=center, ax=axis)

    def _render_pairplot(self, request: StatChartRequest, frame: pd.DataFrame) -> Figure:
        """渲染受限 pairplot，并返回底层 Figure。"""
        numeric_columns = list(frame.select_dtypes(include="number").columns)
        plot_vars = numeric_columns[:6]
        grid = sns.pairplot(
            frame,
            vars=plot_vars,
            hue=request.mapping.hue,
            corner=True,
            palette=PALETTE_BY_ROLE["categorical"],
        )
        grid.figure.set_size_inches(*figure_size(request.style_profile))
        if request.title:
            grid.figure.suptitle(request.title)
        return grid.figure

    def _apply_axes_postprocess(self, request: StatChartRequest, axis: plt.Axes) -> None:
        """统一处理标题、标签、log 坐标和学术风后处理。"""
        mapping = request.mapping
        axis.set_title(request.title or self._default_title(request.chart_kind))
        axis.set_xlabel(request.x_label or mapping.x or "")
        axis.set_ylabel(request.y_label or mapping.y or "")
        if request.options.log_x:
            axis.set_xscale("log")
        if request.options.log_y:
            axis.set_yscale("log")
        if request.chart_kind not in {"heatmap", "correlation_heatmap"}:
            sns.despine(ax=axis)
        self._rotate_category_ticks_if_needed(mapping, axis)

    def _rotate_category_ticks_if_needed(self, mapping: StatChartMapping, axis: plt.Axes) -> None:
        """分类标签较长时旋转，避免重叠。"""
        if not mapping.x:
            return
        labels = [label.get_text() for label in axis.get_xticklabels()]
        if any(len(label) > 10 for label in labels):
            axis.tick_params(axis="x", labelrotation=30)

    def _add_data_labels_if_needed(self, request: StatChartRequest, axis: plt.Axes) -> None:
        """第一版只给 bar/count 添加简单柱顶标签。"""
        if not request.options.show_data_labels:
            return
        for patch in axis.patches:
            height = patch.get_height()
            if pd.isna(height):
                continue
            axis.annotate(
                f"{height:g}",
                (patch.get_x() + patch.get_width() / 2, height),
                ha="center",
                va="bottom",
                fontsize=8,
                xytext=(0, 2),
                textcoords="offset points",
            )

    def _default_title(self, chart_kind: str) -> str:
        return chart_kind.replace("_", " ").title()
