import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import sympy as sp

from chat.application.tools.chart.services.models import (
    ChartTableRequest,
    FunctionPlotRequest,
)
from chat.application.tools.math_solver.services.python_runtime.expression_parser import (
    MathParseError,
    parse_math_expr,
)


_PALETTE = [
    "#4C78A8",
    "#72B7B2",
    "#F58518",
    "#54A24B",
    "#B279A2",
    "#E45756",
    "#9D755D",
    "#BAB0AC",
]


class QuickChartRenderer:
    """普通 session 图表渲染器。

    - 使用 seaborn 承担普通统计图的风格化绘制。
    - 使用 matplotlib 统一标题、caption、annotation、导出。
    - 该 renderer 不负责入参校验，只消费 tool 层归一化后的请求。
    """

    def render_table_chart(self, request: ChartTableRequest, output_path: Path) -> None:
        """渲染普通表格图。

        Args:
            request: 已归一化的表格图请求。
            output_path: 由统一临时文件系统分配的输出路径。
        """
        df = pd.DataFrame(request.rows, columns=request.columns)
        fig, ax = plt.subplots(figsize=self._figure_size(request))
        self._apply_style(ax)

        if request.chart_type == "bar":
            self._render_bar(ax, df, request)
        elif request.chart_type == "line":
            self._render_line(ax, df, request)
        elif request.chart_type == "area":
            self._render_area(ax, df, request)
        elif request.chart_type == "scatter":
            self._render_scatter(ax, df, request)
        elif request.chart_type == "histogram":
            self._render_histogram(ax, df, request)
        elif request.chart_type == "heatmap":
            self._render_heatmap(ax, df, request)
        elif request.chart_type in ("pie", "donut"):
            self._render_pie(ax, df, request)
        elif request.chart_type == "table":
            self._render_table(ax, df)
        elif request.chart_type == "box":
            self._render_box(ax, df, request)
        elif request.chart_type == "violin":
            self._render_violin(ax, df, request)
        else:
            raise ValueError(f"unsupported chart_type: {request.chart_type}")

        self._finish_axes(fig, ax, request)
        fig.savefig(output_path, format=request.output_format, dpi=180, bbox_inches="tight")
        plt.close(fig)

    def render_function_plot(self, request: FunctionPlotRequest, output_path: Path) -> None:
        """渲染函数图。

        Args:
            request: 已归一化的函数图请求。
            output_path: 由统一临时文件系统分配的输出路径。
        """
        x_symbol = sp.Symbol(request.variable)
        x_values = np.linspace(request.domain_start, request.domain_end, request.sample_count)
        fig, ax = plt.subplots(figsize=(8.4, 4.8))
        self._apply_style(ax)

        for index, expression in enumerate(request.expressions):
            try:
                parsed = parse_math_expr(expression, variables=[request.variable])
            except MathParseError as e:
                raise ValueError(f"EXPRESSION_PARSE_FAILED:{index + 1}:{expression}:{e}") from e
            func = sp.lambdify(x_symbol, parsed, modules=["numpy"])
            y_values = np.asarray(func(x_values), dtype=float)
            if y_values.shape == ():
                y_values = np.full_like(x_values, float(y_values))
            if not np.all(np.isfinite(y_values)):
                raise ValueError(f"expression produced nan/inf: {expression}")
            if request.y_log_scale and np.any(y_values <= 0):
                raise ValueError("y_log_scale requires all sampled y values > 0")
            ax.plot(x_values, y_values, label=expression, color=_PALETTE[index % len(_PALETTE)])

        if len(request.expressions) > 1:
            ax.legend(frameon=False)
        ax.set_xlabel(request.x_label or request.variable)
        ax.set_ylabel(request.y_label or "")
        if request.y_log_scale:
            ax.set_yscale("log")
        if request.y_range:
            ax.set_ylim(*request.y_range)
        for ann in request.annotations:
            if ann.type == "hline":
                ax.axhline(float(ann.value), color="#6B7280", linestyle="--", linewidth=1)
                ax.text(x_values[0], float(ann.value), ann.label, fontsize=9, color="#6B7280")
            elif ann.type == "vline":
                ax.axvline(float(ann.value), color="#6B7280", linestyle="--", linewidth=1)
                ax.text(float(ann.value), ax.get_ylim()[1], ann.label, fontsize=9, color="#6B7280")
        fig.suptitle(request.title, fontsize=15, fontweight="bold")
        fig.tight_layout()
        fig.savefig(output_path, format=request.output_format, dpi=180, bbox_inches="tight")
        plt.close(fig)

    def _render_bar(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        melted = df.melt(id_vars=[request.x_column], value_vars=request.y_columns, var_name="series", value_name="value")
        sns.barplot(data=melted, x=request.x_column, y="value", hue="series", ax=ax, palette=_PALETTE[: len(request.y_columns)])

    def _render_line(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        for index, col in enumerate(request.y_columns):
            sns.lineplot(data=df, x=request.x_column, y=col, marker="o", ax=ax, label=col, color=_PALETTE[index % len(_PALETTE)])

    def _render_area(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        x = df[request.x_column].to_numpy()
        is_numeric_x = pd.api.types.is_numeric_dtype(df[request.x_column])
        x_positions = x if is_numeric_x else np.arange(len(x))
        for index, col in enumerate(request.y_columns):
            y = df[col].astype(float).to_numpy()
            ax.plot(x_positions, y, color=_PALETTE[index % len(_PALETTE)], label=col)
            ax.fill_between(x_positions, y, alpha=0.22, color=_PALETTE[index % len(_PALETTE)])
        if not is_numeric_x:
            ax.set_xticks(x_positions)
            ax.set_xticklabels([str(v) for v in x], rotation=0)
        if len(request.y_columns) > 1:
            ax.legend(frameon=False)

    def _render_scatter(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        if request.color_column:
            sns.scatterplot(data=df, x=request.x_column, y=request.y_columns[0], hue=request.color_column, ax=ax, palette=_PALETTE, s=58)
        else:
            sns.scatterplot(data=df, x=request.x_column, y=request.y_columns[0], ax=ax, color=_PALETTE[0], s=58)

    def _render_histogram(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        target = request.y_columns[0] if request.y_columns else request.value_column
        sns.histplot(data=df, x=target, bins=request.bin_count, ax=ax, color=_PALETTE[0], edgecolor="#FFFFFF")

    def _render_heatmap(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        matrix = df.pivot(index=request.y_columns[0], columns=request.x_column, values=request.value_column)
        sns.heatmap(matrix, annot=True, fmt=".3g", cmap="Blues", cbar=False, ax=ax)

    def _render_pie(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        wedges, _, _ = ax.pie(
            df[request.value_column],
            labels=df[request.label_column],
            autopct="%1.1f%%",
            colors=_PALETTE[: len(df)],
            startangle=90,
            wedgeprops={"linewidth": 1, "edgecolor": "white"},
        )
        if request.chart_type == "donut":
            for wedge in wedges:
                wedge.set_width(0.42)
        ax.axis("equal")

    def _render_table(self, ax, df: pd.DataFrame) -> None:
        ax.axis("off")
        table = ax.table(cellText=df.values, colLabels=df.columns, loc="center", cellLoc="center")
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1, 1.35)

    def _render_box(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        sns.boxplot(data=df[request.y_columns], ax=ax, palette=_PALETTE[: len(request.y_columns)])

    def _render_violin(self, ax, df: pd.DataFrame, request: ChartTableRequest) -> None:
        sns.violinplot(data=df[request.y_columns], ax=ax, palette=_PALETTE[: len(request.y_columns)], inner="quartile")

    def _finish_axes(self, fig, ax, request: ChartTableRequest) -> None:
        if request.chart_type not in ("pie", "donut", "table"):
            if request.x_log_scale:
                ax.set_xscale("log")
            if request.y_log_scale:
                ax.set_yscale("log")
            if request.y_range:
                ax.set_ylim(*request.y_range)
            for ann in request.annotations:
                if ann.type == "hline":
                    ax.axhline(float(ann.value), color="#6B7280", linestyle="--", linewidth=1)
                    ax.text(0.02, float(ann.value), ann.label, transform=ax.get_yaxis_transform(), fontsize=9, color="#6B7280")
                elif ann.type == "vline":
                    value = ann.value
                    if request.x_column:
                        x_values = list(pd.DataFrame(request.rows, columns=request.columns)[request.x_column])
                        if value in x_values:
                            value = x_values.index(value)
                    ax.axvline(value, color="#6B7280", linestyle="--", linewidth=1)
            if ax.get_legend() is not None:
                ax.legend(frameon=False)
        ax.set_title(request.title, fontsize=15, fontweight="bold", pad=16)
        if request.caption:
            fig.text(0.5, 0.01, request.caption, ha="center", va="bottom", fontsize=9, color="#6B7280")
        fig.tight_layout(rect=(0, 0.04 if request.caption else 0, 1, 1))

    def _apply_style(self, ax) -> None:
        # 避免 sns.set_theme 修改全局 rcParams；这里只设置当前 Axes。
        ax.set_facecolor("#FFFFFF")
        ax.grid(True, color="#E5E7EB", linewidth=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    def _figure_size(self, request: ChartTableRequest) -> Tuple[float, float]:
        if request.chart_type == "table":
            return (min(12, max(7, len(request.columns) * 1.4)), min(10, max(3.8, len(request.rows) * 0.36)))
        if request.chart_type == "heatmap":
            return (8, 5.6)
        return (8.4, 4.8)


class TraceableMatplotlibRenderer:
    """可追溯图表渲染器。

    - 仅使用 Matplotlib，不使用 seaborn。
    - 当前 source_map 追到 Note block，并尽量补充 row_index/column_name。
    - TODO: 前端 Chat -> Note 跳转通道后置；SVG data 属性增强可继续完善。
    """

    def render(self, request: ChartTableRequest, output_path: Path) -> Dict[str, Dict[str, Any]]:
        """渲染可追溯 SVG 并返回 source_map。"""
        df = pd.DataFrame(request.rows, columns=request.columns)
        fig, ax = plt.subplots(figsize=(8.4, 4.8))
        ax.set_facecolor("#FFFFFF")
        ax.grid(True, color="#E5E7EB", linewidth=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        source_map: Dict[str, Dict[str, Any]] = {}

        if request.chart_type == "bar":
            x = np.arange(len(df))
            width = 0.72 / max(1, len(request.y_columns))
            for series_idx, col in enumerate(request.y_columns):
                offset = (series_idx - (len(request.y_columns) - 1) / 2) * width
                bars = ax.bar(x + offset, df[col].astype(float), width=width, label=col, color=_PALETTE[series_idx % len(_PALETTE)])
                for row_idx, bar in enumerate(bars):
                    element_id = f"bar_{series_idx}_{row_idx}" if len(request.y_columns) > 1 else f"bar_{row_idx}"
                    bar.set_gid(element_id)
                    source_map[element_id] = {"row_index": row_idx, "column_name": col}
            ax.set_xticks(x)
            ax.set_xticklabels([str(v) for v in df[request.x_column]])
            if len(request.y_columns) > 1:
                ax.legend(frameon=False)
        elif request.chart_type == "line":
            x = np.arange(len(df))
            for series_idx, col in enumerate(request.y_columns):
                ax.plot(x, df[col].astype(float), marker="o", label=col, color=_PALETTE[series_idx % len(_PALETTE)])
                for row_idx, y in enumerate(df[col]):
                    element_id = f"point_{series_idx}_{row_idx}" if len(request.y_columns) > 1 else f"point_{row_idx}"
                    ax.scatter([x[row_idx]], [y], color=_PALETTE[series_idx % len(_PALETTE)], gid=element_id)
                    source_map[element_id] = {"row_index": row_idx, "column_name": col}
            ax.set_xticks(x)
            ax.set_xticklabels([str(v) for v in df[request.x_column]])
            if len(request.y_columns) > 1:
                ax.legend(frameon=False)
        elif request.chart_type == "scatter":
            points = ax.scatter(df[request.x_column], df[request.y_columns[0]], color=_PALETTE[0])
            points.set_gid("scatter_points")
            for row_idx, _ in enumerate(request.rows):
                source_map[f"point_{row_idx}"] = {"row_index": row_idx, "column_name": request.y_columns[0]}
        elif request.chart_type in ("pie", "donut"):
            wedges, _ = ax.pie(df[request.value_column], labels=df[request.label_column], colors=_PALETTE[: len(df)], startangle=90)
            for row_idx, wedge in enumerate(wedges):
                element_id = f"wedge_{row_idx}"
                wedge.set_gid(element_id)
                if request.chart_type == "donut":
                    wedge.set_width(0.42)
                source_map[element_id] = {"row_index": row_idx, "column_name": request.value_column}
            ax.axis("equal")
        elif request.chart_type == "heatmap":
            matrix = df.pivot(index=request.y_columns[0], columns=request.x_column, values=request.value_column)
            image = ax.imshow(matrix.to_numpy(dtype=float), cmap="Blues")
            image.set_gid("heatmap")
            ax.set_xticks(np.arange(len(matrix.columns)))
            ax.set_xticklabels([str(v) for v in matrix.columns])
            ax.set_yticks(np.arange(len(matrix.index)))
            ax.set_yticklabels([str(v) for v in matrix.index])
            for row_idx, row in enumerate(request.rows):
                source_map[f"cell_{row_idx}"] = {"row_index": row_idx, "column_name": request.value_column}
        elif request.chart_type == "table":
            ax.axis("off")
            table = ax.table(cellText=df.values, colLabels=df.columns, loc="center", cellLoc="center")
            table.auto_set_font_size(False)
            table.set_fontsize(9)
            table.scale(1, 1.35)
            for r in range(len(df)):
                for c, col in enumerate(df.columns):
                    source_map[f"cell_{r}_{c}"] = {"row_index": r, "column_name": str(col)}
        else:
            # box/violin/area/histogram 第一版绑定到 block，由 service 补充公共定位。
            if request.chart_type == "area":
                x = np.arange(len(df))
                for idx, col in enumerate(request.y_columns):
                    y = df[col].astype(float).to_numpy()
                    ax.plot(x, y, color=_PALETTE[idx % len(_PALETTE)], label=col)
                    ax.fill_between(x, y, alpha=0.22, color=_PALETTE[idx % len(_PALETTE)])
            elif request.chart_type == "histogram":
                target = request.y_columns[0] if request.y_columns else request.value_column
                ax.hist(df[target].astype(float), bins=request.bin_count, color=_PALETTE[0], edgecolor="white")
            elif request.chart_type == "box":
                ax.boxplot([df[col].astype(float) for col in request.y_columns], labels=request.y_columns)
            elif request.chart_type == "violin":
                ax.violinplot([df[col].astype(float) for col in request.y_columns], showmeans=True)
                ax.set_xticks(np.arange(1, len(request.y_columns) + 1))
                ax.set_xticklabels(request.y_columns)

        ax.set_title(request.title, fontsize=15, fontweight="bold", pad=16)
        if request.caption:
            fig.text(0.5, 0.01, request.caption, ha="center", va="bottom", fontsize=9, color="#6B7280")
        fig.tight_layout(rect=(0, 0.04 if request.caption else 0, 1, 1))
        fig.savefig(output_path, format="svg", bbox_inches="tight")
        plt.close(fig)
        self._inject_svg_data_attributes(output_path, source_map)
        return source_map

    def _inject_svg_data_attributes(self, output_path: Path, source_map: Dict[str, Dict[str, Any]]) -> None:
        """给 SVG 中含 gid 的元素补 data-chart-element-id。

        Matplotlib 会把 gid 写成 id 属性。这里做轻量后处理，方便前端未来绑定点击事件。
        """
        text = output_path.read_text(encoding="utf-8")
        for element_id in source_map:
            text = text.replace(f'id="{element_id}"', f'id="{element_id}" data-chart-element-id="{element_id}"')
        output_path.write_text(text, encoding="utf-8")
