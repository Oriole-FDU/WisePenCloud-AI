from typing import Any

from cycler import cycler

# 色盲友好的学术配色。
# 选择原则：
# 1. 不使用高饱和荧光色，避免 PPT/论文里显得廉价。
# 2. 多条曲线时区分度足够。
# 3. 打印、投影、深浅屏幕下都比较稳。
ACADEMIC_COLOR_CYCLE = [
    "#1F77B4",  # muted blue
    "#D62728",  # muted red
    "#2CA02C",  # muted green
    "#9467BD",  # muted purple
    "#FF7F0E",  # muted orange
    "#17BECF",  # cyan
    "#8C564B",  # brown
    "#7F7F7F",  # gray
]


# 默认学术风。
#
# 适用场景：
# - 对话中普通函数图像
# - 文档插图
# - Markdown / 笔记中的数学图
# - 默认工具输出
#
# 设计目标：
# - 白底、克制、清晰
# - 网格存在但不抢数据主体
# - 曲线颜色区分明确
# - 适合导出 SVG/PNG
# - 不追求“炫”，追求像教材、论文、技术报告里的图
ACADEMIC_DEFAULT_RC = {
    # ---------- Figure / Save ----------
    "figure.facecolor": "white",
    "figure.dpi": 120,
    "savefig.facecolor": "white",
    "savefig.edgecolor": "white",
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.08,
    "savefig.dpi": 240,
    # SVG 中保留文字为 text，便于后续复制、搜索、潜在编辑。
    # 注意：如果某些环境字体缺失，SVG 显示可能依赖前端字体兜底。
    "svg.fonttype": "none",
    # PDF/PS 中尽量使用 TrueType 字体，避免文字被转成路径。
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    # ---------- Font ----------
    # DejaVu Sans 是 matplotlib 默认兼容字体；
    # 后面的中文字体用于标题里出现中文时兜底。
    "font.family": "sans-serif",
    "font.sans-serif": [
        "DejaVu Sans",
        "Noto Sans CJK SC",
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
    ],
    "font.size": 11,
    "mathtext.fontset": "dejavusans",
    # 避免中文字体环境下负号显示成方块。
    "axes.unicode_minus": False,
    # ---------- Axes ----------
    "axes.facecolor": "white",
    "axes.edgecolor": "#333333",
    "axes.linewidth": 0.9,
    "axes.axisbelow": True,
    # 去掉上、右边框是常见学术图风格：
    # 减少非数据信息，让视线集中在曲线和坐标关系上。
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.titlesize": 13,
    "axes.titleweight": "semibold",
    "axes.titlepad": 10,
    "axes.labelsize": 11,
    "axes.labelpad": 6,
    # ---------- Grid ----------
    # 学术图需要网格辅助读数，但网格不能压过数据线。
    "axes.grid": True,
    "grid.color": "#D9D9D9",
    "grid.alpha": 0.45,
    "grid.linewidth": 0.75,
    "grid.linestyle": "-",
    # ---------- Ticks ----------
    "xtick.direction": "out",
    "ytick.direction": "out",
    "xtick.color": "#333333",
    "ytick.color": "#333333",
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "xtick.major.size": 3.5,
    "ytick.major.size": 3.5,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.minor.size": 2.0,
    "ytick.minor.size": 2.0,
    # ---------- Lines ----------
    # 2.2 在网页预览、PNG、PPT 中都比较清楚；
    # 太细会显得虚，太粗会像商业大屏图。
    "lines.linewidth": 2.2,
    "lines.markersize": 5.0,
    "lines.solid_capstyle": "round",
    "lines.solid_joinstyle": "round",
    # ---------- Colors ----------
    "axes.prop_cycle": cycler(color=ACADEMIC_COLOR_CYCLE),
    # ---------- Legend ----------
    # 默认无边框，更接近论文图风格。
    "legend.frameon": False,
    "legend.fontsize": 10,
    "legend.handlelength": 2.2,
    "legend.handletextpad": 0.6,
    "legend.borderaxespad": 0.8,
    "legend.labelspacing": 0.4,
    # ---------- Images / 3D / Contour ----------
    # viridis 是感知均匀色图，适合 surface / contour / heatmap。
    # 不使用 jet/rainbow，避免视觉误导。
    "image.cmap": "viridis",
}


# PPT / 演示模式。
#
# 适用场景：
# - PPT 单页展示
# - 教学演示
# - 投影屏幕
# - 用户要求“放大一点”“适合展示”
#
# 与 default 的区别：
# - 字号更大
# - 线条更粗
# - 标题更明显
# - 图例更易读
#
# 代价：
# - 单页信息密度更低
# - 不适合小尺寸嵌入文档
ACADEMIC_SLIDE_RC = {
    **ACADEMIC_DEFAULT_RC,
    "figure.dpi": 130,
    "savefig.dpi": 260,
    "font.size": 13,
    "axes.titlesize": 17,
    "axes.labelsize": 13,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "axes.linewidth": 1.05,
    "lines.linewidth": 2.8,
    "lines.markersize": 6.0,
    "grid.linewidth": 0.85,
    "grid.alpha": 0.42,
    "legend.fontsize": 11,
    "legend.handlelength": 2.4,
}


# 紧凑模式。
#
# 适用场景：
# - 多图并排
# - 文档中的小图
# - 对话中只需要快速查看趋势
# - 后续嵌入卡片、报告局部区域
#
# 与 default 的区别：
# - 字号更小
# - 线条略细
# - padding 更少
# - 视觉更紧凑
#
# 代价：
# - 不适合投影
# - 不适合复杂多曲线图
ACADEMIC_COMPACT_RC = {
    **ACADEMIC_DEFAULT_RC,
    "figure.dpi": 120,
    "savefig.dpi": 220,
    "savefig.pad_inches": 0.04,
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "axes.titlepad": 7,
    "axes.labelpad": 4,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "xtick.major.size": 3.0,
    "ytick.major.size": 3.0,
    "axes.linewidth": 0.8,
    "lines.linewidth": 1.8,
    "lines.markersize": 4.0,
    "grid.linewidth": 0.65,
    "grid.alpha": 0.35,
    "legend.fontsize": 8,
    "legend.handlelength": 1.8,
    "legend.handletextpad": 0.45,
    "legend.labelspacing": 0.3,
}


STYLE_PROFILES = {
    "academic_default": ACADEMIC_DEFAULT_RC,
    "academic_slide": ACADEMIC_SLIDE_RC,
    "academic_compact": ACADEMIC_COMPACT_RC,
}

FIGURE_SIZES = {
    "academic_default": (7.2, 4.2),
    "academic_slide": (9.6, 5.4),
    "academic_compact": (5.2, 3.2),
}

SURFACE_FIGURE_SIZES = {
    "academic_default": (7.2, 5.6),
    "academic_slide": (9.6, 6.6),
    "academic_compact": (5.8, 4.4),
}


def get_style_profile(style_profile: str) -> dict[str, Any]:
    if style_profile not in STYLE_PROFILES:
        raise ValueError(f"Unsupported style_profile: {style_profile}")
    return STYLE_PROFILES[style_profile]
