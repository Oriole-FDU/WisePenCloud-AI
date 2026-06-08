import json
from typing import Any, Dict

from chat.application.tools.chart.services.stat_chart.errors import StatChartError
from chat.application.tools.chart.services.stat_chart.models import (
    StatChartDataInput,
    StatChartMapping,
    StatChartOptions,
    StatChartRequest,
)
from chat.application.tools.chart.services.stat_chart.service import StatChartService
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

_CHART_KINDS = [
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
]

_MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "x": {"type": ["string", "null"]},
        "y": {"type": ["string", "null"]},
        "value": {"type": ["string", "null"]},
        "hue": {"type": ["string", "null"]},
        "style": {"type": ["string", "null"]},
        "size": {"type": ["string", "null"]},
        "row": {"type": ["string", "null"]},
        "col": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

_OPTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "bins": {"type": ["integer", "null"], "minimum": 5, "maximum": 100},
        "kde": {"type": "boolean", "default": False},
        "estimator": {"type": "string", "enum": ["mean", "median", "sum", "count"], "default": "mean"},
        "errorbar": {"type": ["string", "null"], "enum": ["ci", "sd", "se", None], "default": "ci"},
        "confidence_level": {"type": "integer", "minimum": 50, "maximum": 99, "default": 95},
        "regression_order": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
        "robust": {"type": "boolean", "default": False},
        "lowess": {"type": "boolean", "default": False},
        "log_x": {"type": "boolean", "default": False},
        "log_y": {"type": "boolean", "default": False},
        "show_data_labels": {"type": "boolean", "default": False},
        "sort_categories": {"type": "string", "enum": ["none", "ascending", "descending"], "default": "none"},
        "annot": {"type": "boolean", "default": False},
        "center_zero": {"type": "boolean", "default": False},
    },
    "additionalProperties": False,
}

_STAT_CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "chart_kind": {"type": "string", "enum": _CHART_KINDS},
        "data": {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {"type": "object"},
                    "minItems": 1,
                    "maxItems": 5000,
                },
                "columns": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "maxItems": 50,
                },
            },
            "required": ["records"],
            "additionalProperties": False,
        },
        "mapping": _MAPPING_SCHEMA,
        "options": _OPTIONS_SCHEMA,
        "title": {"type": ["string", "null"], "maxLength": 120},
        "x_label": {"type": ["string", "null"], "maxLength": 80},
        "y_label": {"type": ["string", "null"], "maxLength": 80},
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
    },
    "required": ["chart_kind", "data"],
    "additionalProperties": False,
}


class StatChartTool(BaseTool):
    """结构化数据统计图 tool。"""

    def __init__(self, *, service: StatChartService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "stat_chart"

    @property
    def description(self) -> str:
        return (
            "Render statistical charts from structured tabular data. Use this tool for scatter plots, "
            "line plots, histograms, KDE/ECDF plots, bar/count plots, box/violin/boxen plots, "
            "regression plots, heatmaps, correlation heatmaps, and simple faceted statistical "
            "visualizations. The tool accepts data and declarative chart specifications only; it "
            "does not execute user-provided Python code or arbitrary plotting scripts. Do not use "
            "it for symbolic mathematical function plots, provenance-aware charts, or custom "
            "matplotlib/seaborn code execution."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _STAT_CHART_SCHEMA

    @property
    def namespaces(self) -> tuple[str, ...]:
        return ("chart",)

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        """执行统计图渲染。"""
        session_id = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        try:
            request = _request_from_kwargs(kwargs)
            result = self._service.render(request)
        except StatChartError as exc:
            return f"[Tool Error] stat_chart failed: {exc}"
        except Exception as exc:
            log_fail("stat_chart", exc, session_id=session_id)
            return "[Tool Error] stat_chart failed: unexpected rendering error."

        return "\n".join(
            [
                "[Tool Result] stat_chart",
                "",
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            ]
        )


def _request_from_kwargs(kwargs: dict[str, Any]) -> StatChartRequest:
    """将 LLM kwargs 收敛成内部可信 request。"""
    data = kwargs["data"]
    mapping = kwargs.get("mapping") or {}
    options = kwargs.get("options") or {}
    return StatChartRequest(
        chart_kind=kwargs["chart_kind"],
        data=StatChartDataInput(
            records=list(data["records"]),
            columns=list(data["columns"]) if data.get("columns") is not None else None,
        ),
        mapping=StatChartMapping(
            x=_none_if_blank(mapping.get("x")),
            y=_none_if_blank(mapping.get("y")),
            value=_none_if_blank(mapping.get("value")),
            hue=_none_if_blank(mapping.get("hue")),
            style=_none_if_blank(mapping.get("style")),
            size=_none_if_blank(mapping.get("size")),
            row=_none_if_blank(mapping.get("row")),
            col=_none_if_blank(mapping.get("col")),
        ),
        options=StatChartOptions(
            bins=options.get("bins"),
            kde=bool(options.get("kde", False)),
            estimator=options.get("estimator", "mean"),
            errorbar=options.get("errorbar", "ci"),
            confidence_level=int(options.get("confidence_level", 95)),
            regression_order=int(options.get("regression_order", 1)),
            robust=bool(options.get("robust", False)),
            lowess=bool(options.get("lowess", False)),
            log_x=bool(options.get("log_x", False)),
            log_y=bool(options.get("log_y", False)),
            show_data_labels=bool(options.get("show_data_labels", False)),
            sort_categories=options.get("sort_categories", "none"),
            annot=bool(options.get("annot", False)),
            center_zero=bool(options.get("center_zero", False)),
        ),
        title=_none_if_blank(kwargs.get("title")),
        x_label=_none_if_blank(kwargs.get("x_label")),
        y_label=_none_if_blank(kwargs.get("y_label")),
        output_formats=list(dict.fromkeys(kwargs.get("output_formats") or ["svg", "png"])),
        style_profile=kwargs.get("style_profile", "academic_default"),
    )


def _none_if_blank(value: Any) -> str | None:
    """将空字符串归一为 None，避免 renderer 误当列名。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
