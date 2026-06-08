from typing import Any

from chat.application.tools.chart.services.function_plot.styles import (
    FIGURE_SIZES,
    get_style_profile as get_base_style_profile,
)

STAT_CHART_RC_EXTENSIONS = {
    "patch.linewidth": 0.8,
    "boxplot.boxprops.linewidth": 1.0,
    "boxplot.whiskerprops.linewidth": 1.0,
    "boxplot.capprops.linewidth": 1.0,
    "boxplot.medianprops.linewidth": 1.4,
}

PALETTE_BY_ROLE = {
    "categorical": "colorblind",
    "sequential": "viridis",
    "diverging": "vlag",
}


def get_style_profile(style_profile: str) -> dict[str, Any]:
    """返回统计图使用的学术风 rcParams。

    统计图复用函数图的学术风基础，再叠加 patch/boxplot 这类统计图元素的线宽。
    """
    return {
        **get_base_style_profile(style_profile),
        **STAT_CHART_RC_EXTENSIONS,
    }


def figure_size(style_profile: str, *, square: bool = False) -> tuple[float, float]:
    """返回统计图默认尺寸；热力图等方形图使用独立比例。"""
    if square:
        size = FIGURE_SIZES[style_profile][1]
        return (size, size)
    return FIGURE_SIZES[style_profile]
