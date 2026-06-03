"""内测功能"""

from typing import Any, Dict, List, Optional, Tuple

from chat.application.tools.chart.services.formatting import (
    format_chart_error,
    format_chart_result,
    tool_error,
)
from chat.application.tools.chart.services.models import (
    Annotation,
    ChartError,
    ChartTableRequest,
    FunctionPlotRequest,
)
from chat.application.tools.chart.services.service import (
    QuickChartService,
    TraceableChartService,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail


_ANNOTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["hline", "vline"]},
        "value": {"type": ["number", "string"]},
        "label": {"type": "string"},
    },
    "required": ["type", "value", "label"],
    "additionalProperties": False,
}

_TABLE_CHART_TYPES = ["bar", "line", "area", "scatter", "histogram", "heatmap", "pie", "donut", "table", "box", "violin"]

_TABLE_CHART_SCHEMA_PROPS = {
    "chart_type": {"type": "string", "enum": _TABLE_CHART_TYPES},
    "title": {"type": "string", "minLength": 1},
    "caption": {"type": ["string", "null"]},
    "columns": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
    "rows": {"type": "array", "items": {"type": "array"}, "minItems": 1},
    "x_column": {"type": ["string", "null"]},
    "y_columns": {"type": "array", "items": {"type": "string"}},
    "label_column": {"type": ["string", "null"]},
    "value_column": {"type": ["string", "null"]},
    "color_column": {"type": ["string", "null"]},
    "bin_count": {"type": ["integer", "null"]},
    "annotations": {"type": "array", "items": _ANNOTATION_SCHEMA, "maxItems": 5},
    "x_log_scale": {"type": "boolean"},
    "y_log_scale": {"type": "boolean"},
    "y_range": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
    "output_format": {"type": "string", "enum": ["png", "svg"]},
}

_QUICK_TABLE_SCHEMA = {
    "type": "object",
    "properties": _TABLE_CHART_SCHEMA_PROPS,
    "required": ["chart_type", "title", "columns", "rows", "output_format"],
    "additionalProperties": False,
}

_FUNCTION_SCHEMA = {
    "type": "object",
    "properties": {
        "expressions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "expression": {"type": ["string", "null"]},
        "variable": {"type": "string"},
        "domain_start": {"type": "number"},
        "domain_end": {"type": "number"},
        "sample_count": {"type": "integer", "minimum": 50, "maximum": 2000},
        "title": {"type": "string", "minLength": 1},
        "x_label": {"type": ["string", "null"]},
        "y_label": {"type": ["string", "null"]},
        "annotations": {"type": "array", "items": _ANNOTATION_SCHEMA, "maxItems": 5},
        "y_log_scale": {"type": "boolean"},
        "y_range": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "output_format": {"type": "string", "enum": ["png", "svg"]},
    },
    "required": ["variable", "domain_start", "domain_end", "sample_count", "title", "output_format"],
    "additionalProperties": False,
}

_TRACEABLE_SCHEMA_PROPS = dict(_TABLE_CHART_SCHEMA_PROPS)
_TRACEABLE_SCHEMA_PROPS.update(
    {
        "resource_kind": {"type": "string", "enum": ["internal_note", "parsed_note"]},
        "resource_id": {"type": "string", "minLength": 1},
        "resource_version": {"type": ["string", "null"]},
        "block_id": {"type": "string", "minLength": 1},
        "output_format": {"type": "string", "enum": ["svg"]},
    }
)
_TRACEABLE_SCHEMA_PROPS.pop("columns")
_TRACEABLE_SCHEMA_PROPS.pop("rows")

_TRACEABLE_SCHEMA = {
    "type": "object",
    "properties": _TRACEABLE_SCHEMA_PROPS,
    "required": ["resource_kind", "resource_id", "block_id", "chart_type", "title", "output_format"],
    "additionalProperties": False,
}


def _annotations(items: Optional[List[Dict[str, Any]]]) -> List[Annotation]:
    return [
        Annotation(type=item["type"], value=item["value"], label=item["label"])
        for item in (items or [])
    ]


def _y_range(raw: Optional[List[float]]) -> Optional[Tuple[float, float]]:
    if raw is None:
        return None
    return (float(raw[0]), float(raw[1]))


def _table_request_from_kwargs(kwargs: Dict[str, Any], *, traceable: bool = False) -> ChartTableRequest:
    return (
        ChartTableRequest(
            chart_type=kwargs["chart_type"],
            title=kwargs["title"].strip(),
            caption=kwargs.get("caption"),
            columns=list(kwargs.get("columns") or []),
            rows=[list(row) for row in kwargs.get("rows", [])],
            x_column=kwargs.get("x_column"),
            y_columns=list(kwargs.get("y_columns") or []),
            label_column=kwargs.get("label_column"),
            value_column=kwargs.get("value_column"),
            color_column=kwargs.get("color_column"),
            bin_count=kwargs.get("bin_count"),
            annotations=_annotations(kwargs.get("annotations")),
            x_log_scale=bool(kwargs.get("x_log_scale", False)),
            y_log_scale=bool(kwargs.get("y_log_scale", False)),
            y_range=_y_range(kwargs.get("y_range")),
            output_format="svg" if traceable else kwargs.get("output_format", "png"),
        )
    )


def _function_request_from_kwargs(kwargs: Dict[str, Any]) -> Tuple[Optional[FunctionPlotRequest], Optional[str]]:
    expression = kwargs.get("expression")
    expressions = list(kwargs.get("expressions") or [])
    if not expressions and expression:
        expressions = [expression]
    if expression and expressions and expression not in expressions:
        return None, "expression 与 expressions 不一致。"
    return (
        FunctionPlotRequest(
            expressions=expressions,
            variable=kwargs.get("variable", "x"),
            domain_start=float(kwargs["domain_start"]),
            domain_end=float(kwargs["domain_end"]),
            sample_count=int(kwargs["sample_count"]),
            title=kwargs["title"].strip(),
            x_label=kwargs.get("x_label"),
            y_label=kwargs.get("y_label"),
            annotations=_annotations(kwargs.get("annotations")),
            y_log_scale=bool(kwargs.get("y_log_scale", False)),
            y_range=_y_range(kwargs.get("y_range")),
            output_format=kwargs.get("output_format", "png"),
        ),
        None,
    )


class QuickChartFromTableTool(BaseTool):
    """普通表格图 tool。"""

    def __init__(self, *, service: QuickChartService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "quick_chart_from_table"

    @property
    def description(self) -> str:
        return "Draw a quick chart from explicit table data in the current session. Returns an image_file_ref and mock_preview_markdown."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _QUICK_TABLE_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        """执行普通表格图绘制。"""
        session_id = context.get("session_id")
        user_id = context.get("user_id")
        if not session_id:
            return tool_error("Missing session_id in execution context.")
        if not user_id:
            return tool_error("Missing user_id in execution context.")

        request = _table_request_from_kwargs(kwargs)
        try:
            result = await self._service.render_table_chart(
                user_id=user_id,
                session_id=session_id,
                request=request,
            )
            return format_chart_result(result)
        except Exception as e:
            log_fail("quick_chart_from_table", e, session_id=session_id)
            return format_chart_error(
                ChartError("RENDER_FAILED", f"图表渲染失败：{e}", None, True)
            )


class QuickFunctionPlotTool(BaseTool):
    """普通函数图 tool。"""

    def __init__(self, *, service: QuickChartService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "quick_function_plot"

    @property
    def description(self) -> str:
        return "Draw one-variable mathematical functions safely using SymPy sampling and Matplotlib rendering."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _FUNCTION_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        """执行函数图绘制。"""
        session_id = context.get("session_id")
        user_id = context.get("user_id")
        if not session_id:
            return tool_error("Missing session_id in execution context.")
        if not user_id:
            return tool_error("Missing user_id in execution context.")
        request, error = _function_request_from_kwargs(kwargs)
        if error:
            return tool_error(error, field="expression")
        try:
            result = await self._service.render_function_plot(
                user_id=user_id,
                session_id=session_id,
                request=request,  # type: ignore[arg-type]
            )
            return format_chart_result(result)
        except ValueError as e:
            message = str(e)
            if message.startswith("EXPRESSION_PARSE_FAILED:"):
                _, index, expression, _detail = message.split(":", 3)
                return format_chart_error(
                    ChartError(
                        "EXPRESSION_PARSE_FAILED",
                        f"第 {index} 个表达式 '{expression}' 无法解析",
                        "expressions",
                        True,
                    )
                )
            return format_chart_error(ChartError("RENDER_FAILED", f"函数图渲染失败：{e}", None, True))
        except Exception as e:
            log_fail("quick_function_plot", e, session_id=session_id)
            return format_chart_error(ChartError("RENDER_FAILED", f"函数图渲染失败：{e}", None, True))


class TraceableChartFromNoteTool(BaseTool):
    """可追溯 Note 图表 tool。"""

    def __init__(self, *, service: TraceableChartService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "traceable_chart_from_note"

    @property
    def description(self) -> str:
        return "Draw a traceable SVG chart from an internal or parsed Note block table. Returns source_map for future Chat-to-Note navigation."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TRACEABLE_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        """执行可追溯 Note 图绘制。"""
        session_id = context.get("session_id")
        user_id = context.get("user_id")
        if not session_id:
            return tool_error("Missing session_id in execution context.", result_type="traceable_chart_error")
        if not user_id:
            return tool_error("Missing user_id in execution context.", result_type="traceable_chart_error")

        request = _table_request_from_kwargs({**kwargs, "columns": [], "rows": []}, traceable=True)
        try:
            result = await self._service.render_from_note(
                user_id=user_id,
                session_id=session_id,
                resource_kind=kwargs["resource_kind"],
                resource_id=kwargs["resource_id"],
                resource_version=kwargs.get("resource_version"),
                block_id=kwargs["block_id"],
                request=request,
            )
            return format_chart_result(result)
        except ValueError as e:
            if str(e) == "NOTE_TABLE_NOT_FOUND":
                return tool_error("未找到可用于绘图的 Note block 表格。", field="block_id", result_type="traceable_chart_error")
            return format_chart_error(ChartError("RENDER_FAILED", f"可追溯图渲染失败：{e}", None, True), result_type="traceable_chart_error")
        except Exception as e:
            log_fail("traceable_chart_from_note", e, session_id=session_id)
            return format_chart_error(ChartError("RENDER_FAILED", f"可追溯图渲染失败：{e}", None, True), result_type="traceable_chart_error")
