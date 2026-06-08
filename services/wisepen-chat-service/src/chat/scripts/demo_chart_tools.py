import asyncio
import json
import math
from typing import Any, Dict, List

from chat.application.tools.chart.chart_tools import (
    QuickChartFromTableTool,
    QuickFunctionPlotTool,
    TraceableChartFromNoteTool,
)
from chat.application.tools.chart.services.note_provider import MockNoteTableProvider
from chat.application.tools.chart.services.output_adapter import build_default_chart_output_adapter
from chat.application.tools.chart.services.renderer import QuickChartRenderer, TraceableMatplotlibRenderer
from chat.application.tools.chart.services.service import QuickChartService, TraceableChartService


def build_tools() -> Dict[str, Any]:
    """构建 demo 用工具实例。

    - 使用仓库统一临时文件根目录。
    - 不写源码目录。
    - 不依赖正式前端。
    """
    output_adapter = build_default_chart_output_adapter()
    quick_service = QuickChartService(
        output_adapter=output_adapter,
        renderer=QuickChartRenderer(),
    )
    traceable_service = TraceableChartService(
        output_adapter=output_adapter,
        renderer=TraceableMatplotlibRenderer(),
        note_table_provider=MockNoteTableProvider(),
    )
    return {
        "quick_chart_from_table": QuickChartFromTableTool(service=quick_service),
        "quick_function_plot": QuickFunctionPlotTool(service=quick_service),
        "traceable_chart_from_note": TraceableChartFromNoteTool(service=traceable_service),
    }


async def run_demo() -> None:
    """运行图表工具 demo。

    - Demo 1：普通图 gallery。
    - Demo 2：函数图。
    - Demo 3：mock Note 可追溯图。
    """
    tools = build_tools()
    context = {"user_id": "demo_user", "session_id": "demo_session"}

    gallery: List[Dict[str, Any]] = [
        {
            "chart_type": "bar",
            "title": "Grouped Bar",
            "caption": "普通 session grouped bar",
            "columns": ["model", "accuracy", "latency"],
            "rows": [["A", 0.91, 120], ["B", 0.87, 96], ["C", 0.93, 142]],
            "x_column": "model",
            "y_columns": ["accuracy", "latency"],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "line",
            "title": "Multi Line",
            "caption": None,
            "columns": ["day", "A", "B"],
            "rows": [["D1", 3, 2], ["D2", 4, 3], ["D3", 5, 4]],
            "x_column": "day",
            "y_columns": ["A", "B"],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": None,
            "annotations": [{"type": "hline", "value": 3.5, "label": "baseline"}],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "area",
            "title": "Area",
            "caption": None,
            "columns": ["day", "A", "B"],
            "rows": [["D1", 3, 1], ["D2", 5, 2], ["D3", 4, 3]],
            "x_column": "day",
            "y_columns": ["A", "B"],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "scatter",
            "title": "Scatter",
            "caption": None,
            "columns": ["x", "y"],
            "rows": [[1, 2], [2, 3], [3, 2.5], [4, 4]],
            "x_column": "x",
            "y_columns": ["y"],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": None,
            "annotations": [{"type": "vline", "value": 2, "label": "x=2"}],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "histogram",
            "title": "Histogram",
            "caption": None,
            "columns": ["value"],
            "rows": [[1], [2], [2], [3], [4], [4], [4]],
            "x_column": None,
            "y_columns": ["value"],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": 4,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "heatmap",
            "title": "Heatmap",
            "caption": None,
            "columns": ["x", "y", "value"],
            "rows": [["A", "R1", 1], ["B", "R1", 2], ["A", "R2", 3], ["B", "R2", 4]],
            "x_column": "x",
            "y_columns": ["y"],
            "label_column": None,
            "value_column": "value",
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "pie",
            "title": "Pie",
            "caption": None,
            "columns": ["label", "value"],
            "rows": [["A", 40], ["B", 30], ["C", 30]],
            "x_column": None,
            "y_columns": [],
            "label_column": "label",
            "value_column": "value",
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "donut",
            "title": "Donut",
            "caption": None,
            "columns": ["label", "value"],
            "rows": [["A", 40], ["B", 30], ["C", 30]],
            "x_column": None,
            "y_columns": [],
            "label_column": "label",
            "value_column": "value",
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "table",
            "title": "Table",
            "caption": None,
            "columns": ["model", "accuracy"],
            "rows": [["A", 0.91], ["B", 0.87]],
            "x_column": None,
            "y_columns": [],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "box",
            "title": "Box",
            "caption": None,
            "columns": ["A", "B"],
            "rows": [[1, 2], [2, 3], [3, 5], [4, 6]],
            "x_column": None,
            "y_columns": ["A", "B"],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
        {
            "chart_type": "violin",
            "title": "Violin",
            "caption": None,
            "columns": ["A", "B"],
            "rows": [[1, 2], [2, 3], [3, 5], [4, 6]],
            "x_column": None,
            "y_columns": ["A", "B"],
            "label_column": None,
            "value_column": None,
            "color_column": None,
            "bin_count": None,
            "annotations": [],
            "x_log_scale": False,
            "y_log_scale": False,
            "y_range": None,
            "output_format": "png",
        },
    ]

    for item in gallery:
        output = await tools["quick_chart_from_table"].execute(context, **item)
        print(output)

    function_output = await tools["quick_function_plot"].execute(
        context,
        expressions=["sin(x)", "cos(x)"],
        expression=None,
        variable="x",
        domain_start=0,
        domain_end=2 * math.pi,
        sample_count=500,
        title="sin(x) 与 cos(x)",
        x_label="x",
        y_label="y",
        annotations=[],
        y_log_scale=False,
        y_range=[-1.1, 1.1],
        output_format="png",
    )
    print(function_output)

    traceable_output = await tools["traceable_chart_from_note"].execute(
        context,
        resource_kind="internal_note",
        resource_id="note_mock",
        resource_version="v1",
        block_id="block_metrics",
        chart_type="bar",
        title="模型指标对比",
        caption="来自 mock Note block table",
        x_column="model",
        y_columns=["accuracy"],
        label_column=None,
        value_column=None,
        color_column=None,
        bin_count=None,
        annotations=[],
        x_log_scale=False,
        y_log_scale=False,
        y_range=None,
        output_format="svg",
    )
    print(traceable_output)
    parsed = json.loads(traceable_output)
    print(json.dumps(parsed.get("source_map", {}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(run_demo())
