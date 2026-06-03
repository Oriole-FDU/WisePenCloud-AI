import json
from typing import Any, Dict, Optional

from chat.application.tools.chart.services.models import ChartError, ChartRenderPayload


def format_chart_error(error: ChartError, *, result_type: str = "quick_chart_error") -> str:
    """格式化图表错误。

    Args:
        error: 结构化错误。
        result_type: quick_chart_error 或 traceable_chart_error。

    Returns:
        JSON 字符串，便于 mock 和前端解析。
    """
    return json.dumps(
        {
            "type": result_type,
            "error_code": error.error_code,
            "message": error.message,
            "field": error.field,
            "recoverable": error.recoverable,
        },
        ensure_ascii=False,
        indent=2,
    )


def format_chart_result(payload: ChartRenderPayload) -> str:
    """格式化图表成功结果。

    Args:
        payload: 渲染服务返回的图表结果。

    Returns:
        JSON 字符串，包含 image_file_ref 与 mock_preview_markdown。
    """
    result: Dict[str, Any] = {
        "type": "traceable_chart_result" if payload.traceable else "quick_chart_result",
        "chart_type": payload.chart_type,
        "title": payload.title,
        "output_format": payload.output_format,
        "image_file_ref": payload.generated.image_file_ref,
        # mock 字段：当前内测阶段方便人工 review；正式字段使用 image_file_ref。
        "image_path": str(payload.generated.file_path),
        "mock_preview_markdown": f"![{payload.title}]({payload.generated.preview_url})",
        "source_mode": payload.source_mode,
        "traceable": payload.traceable,
    }
    if payload.source_map is not None:
        result["source_map"] = payload.source_map
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_error(
    message: str,
    *,
    error_code: str = "VALIDATION_FAILED",
    field: Optional[str] = None,
    result_type: str = "quick_chart_error",
) -> str:
    """快速构造 tool 边界错误。"""
    return format_chart_error(
        ChartError(
            error_code=error_code,
            message=message,
            field=field,
            recoverable=True,
        ),
        result_type=result_type,
    )
