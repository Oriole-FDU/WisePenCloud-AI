import json
import math
from typing import Any, Dict

from chat.application.tools.chart.services.function_plot.errors import FunctionPlotError
from chat.application.tools.chart.services.function_plot.models import (
    FunctionPlotRequest,
)
from chat.application.tools.chart.services.function_plot.service import (
    FunctionPlotService,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

_FUNCTION_PLOT_SCHEMA = {
    "type": "object",
    "properties": {
        "plot_kind": {
            "type": "string",
            "enum": ["line_2d", "surface_3d", "contour_2d"],
            "default": "line_2d",
        },
        "expressions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
            "minItems": 1,
            "maxItems": 5,
        },
        "variables": {
            "type": "array",
            "items": {"type": "string", "enum": ["x", "y", "t"]},
            "minItems": 1,
            "maxItems": 2,
            "default": ["x"],
        },
        "x_range": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 2,
            "maxItems": 2,
            "default": [-10.0, 10.0],
        },
        "y_range": {
            "type": ["array", "null"],
            "items": {"type": "number"},
            "minItems": 2,
            "maxItems": 2,
        },
        "samples": {"type": "integer", "minimum": 200, "maximum": 6000, "default": 1600},
        "output_formats": {
            "type": "array",
            "items": {"type": "string", "enum": ["svg", "png"]},
            "minItems": 1,
            "maxItems": 2,
            "default": ["svg", "png"],
        },
        "style_profile": {
            "type": "string",
            "enum": ["academic_default", "academic_slide", "academic_compact"],
            "default": "academic_default",
        },
        "detect_roots": {"type": "boolean", "default": False},
        "detect_extrema": {"type": "boolean", "default": False},
        "title": {"type": ["string", "null"], "maxLength": 120},
    },
    "required": ["expressions"],
    "additionalProperties": False,
}


class FunctionPlotTool(BaseTool):

    def __init__(self, *, service: FunctionPlotService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "function_plot"

    @property
    def description(self) -> str:
        return (
            "Render mathematical function plots from symbolic expressions. "
            "Use this tool for ordinary single-variable function graphs, multi-function comparisons, "
            "two-variable 3D surface plots, contour plots, and optional root or local extrema annotations. "
            "The tool only accepts mathematical expressions and does not execute user-provided Python code. "
            "Do not use it for statistical charts, data-table visualizations, arbitrary plotting scripts, "
            "or provenance-aware chart generation."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _FUNCTION_PLOT_SCHEMA

    @property
    def namespaces(self) -> tuple[str, ...]:
        return ("math_solver", "chart")

    @property
    def search_hint(self) -> str:
        return (
            "Plot mathematical functions from symbolic expressions. Supports line_2d, surface_3d, "
            "contour_2d, roots and extrema annotations. Belongs to math_solver and chart."
        )

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        """执行函数绘图 tool。

        Args:
            context: 系统注入上下文，至少需要 `session_id`。
            **kwargs: LLM 按 `parameters_schema` 生成的业务参数。

        Returns:
            成功时返回 `[Tool Result] function_plot` 加 JSON payload；失败时返回
            `[Tool Error] ...`。
        """
        session_id = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        try:
            request = _request_from_kwargs(kwargs)
            result = self._service.render(request)
        except FunctionPlotError as exc:
            return f"[Tool Error] function_plot failed: {exc}"
        except Exception as exc:
            log_fail("function_plot", exc, session_id=session_id)
            return "[Tool Error] function_plot failed: unexpected rendering error."

        return "\n".join(
            [
                "[Tool Result] function_plot",
                "",
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            ]
        )


def _request_from_kwargs(kwargs: dict[str, Any]) -> FunctionPlotRequest:
    """将 LLM tool 参数收敛为内部可信 request。

    Args:
        kwargs: LLM tool call 参数。

    Returns:
        已填充默认值、完成跨字段约束处理的 `FunctionPlotRequest`。

    Raises:
        FunctionPlotError: 当参数组合无法安全执行时抛出。
    """
    plot_kind = kwargs.get("plot_kind", "line_2d")
    expressions = [str(item).strip() for item in kwargs["expressions"]]

    # JSON schema 能约束单字段枚举和长度；这里保留跨字段约束，避免二元图收到多表达式。
    if plot_kind in {"surface_3d", "contour_2d"} and len(expressions) != 1:
        raise FunctionPlotError("surface_3d and contour_2d require exactly one expression.")

    variables = kwargs.get("variables")
    if variables is None:
        variables = ["x", "y"] if plot_kind in {"surface_3d", "contour_2d"} else ["x"]

    # 二元图的变量顺序会直接决定 meshgrid 语义，因此必须在 tool 边界固定。
    if plot_kind in {"surface_3d", "contour_2d"} and variables != ["x", "y"]:
        raise FunctionPlotError('surface_3d and contour_2d require variables ["x", "y"].')

    samples = int(kwargs.get("samples", 1600))
    if plot_kind in {"surface_3d", "contour_2d"} and samples > 250:
        raise FunctionPlotError("surface_3d and contour_2d samples must be less than or equal to 250.")

    output_formats = kwargs.get("output_formats")
    if output_formats is None:
        output_formats = ["png"] if plot_kind == "surface_3d" else ["svg", "png"]

    title = kwargs.get("title")
    if title is not None:
        title = str(title).strip() or None

    return FunctionPlotRequest(
        plot_kind=plot_kind,
        expressions=expressions,
        variables=list(variables),
        x_range=_range_pair(kwargs.get("x_range", [-10.0, 10.0]), "x_range"),
        y_range=(
            _range_pair(kwargs.get("y_range", [-10.0, 10.0]), "y_range")
            if plot_kind in {"surface_3d", "contour_2d"}
            else _optional_range_pair(kwargs.get("y_range"), "y_range")
        ),
        samples=samples,
        output_formats=list(dict.fromkeys(output_formats)),
        style_profile=kwargs.get("style_profile", "academic_default"),
        detect_roots=bool(kwargs.get("detect_roots", False)),
        detect_extrema=bool(kwargs.get("detect_extrema", False)),
        title=title,
    )


def _optional_range_pair(raw: Any, field_name: str) -> tuple[float, float] | None:
    """转换可空范围字段。"""
    if raw is None:
        return None
    return _range_pair(raw, field_name)


def _range_pair(raw: Any, field_name: str) -> tuple[float, float]:
    """转换图像坐标范围，并保证上下界可用于采样。

    这里保留有限数和递增检查，因为 JSON schema 只能表达两个 number，
    不能表达 `start < end` 或 NaN/Inf 排除。
    """
    start = float(raw[0])
    end = float(raw[1])
    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise FunctionPlotError(f"{field_name} must be finite and increasing.")
    return (start, end)
