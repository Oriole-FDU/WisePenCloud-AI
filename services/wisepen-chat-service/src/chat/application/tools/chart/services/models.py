from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ChartErrorCode = str


@dataclass(frozen=True, slots=True)
class ChartError:
    """结构化图表错误。

    Attributes:
        error_code: 错误码，供 tool 输出稳定结构。
        message: 可让模型转述给用户的中文错误。
        field: 出错字段；无法归因时为 None。
        recoverable: 用户修正输入后是否可恢复。
    """

    error_code: ChartErrorCode
    message: str
    field: Optional[str] = None
    recoverable: bool = True


@dataclass(frozen=True, slots=True)
class Annotation:
    """图表标注线。"""

    type: str
    value: Any
    label: str


@dataclass(frozen=True, slots=True)
class ChartTableRequest:
    """普通表格图请求。"""

    chart_type: str
    title: str
    caption: Optional[str]
    columns: List[str]
    rows: List[List[Any]]
    x_column: Optional[str]
    y_columns: List[str]
    label_column: Optional[str]
    value_column: Optional[str]
    color_column: Optional[str]
    bin_count: Optional[int]
    annotations: List[Annotation]
    x_log_scale: bool
    y_log_scale: bool
    y_range: Optional[Tuple[float, float]]
    output_format: str


@dataclass(frozen=True, slots=True)
class FunctionPlotRequest:
    """函数图请求。"""

    expressions: List[str]
    variable: str
    domain_start: float
    domain_end: float
    sample_count: int
    title: str
    x_label: Optional[str]
    y_label: Optional[str]
    annotations: List[Annotation]
    y_log_scale: bool
    y_range: Optional[Tuple[float, float]]
    output_format: str


@dataclass(frozen=True, slots=True)
class GeneratedChartFile:
    """已生成的图表文件元数据。"""

    file_path: Path
    storage_file_name: str
    file_name: str
    user_id: str
    session_id: str
    content_type: str
    output_format: str
    size_bytes: int
    image_file_ref: str
    preview_url: str


@dataclass(frozen=True, slots=True)
class ChartRenderPayload:
    """图表渲染结果。"""

    chart_type: str
    title: str
    output_format: str
    generated: GeneratedChartFile
    source_mode: str
    traceable: bool
    source_map: Optional[Dict[str, Dict[str, Any]]] = None


@dataclass(frozen=True, slots=True)
class NoteTable:
    """从 Note block 抽取出的表格。"""

    resource_kind: str
    resource_id: str
    resource_version: Optional[str]
    block_id: str
    columns: List[str]
    rows: List[List[Any]]


class ChartToolError(Exception):
    """图表工具异常。

    Args:
        error: 结构化错误对象。
    """

    def __init__(self, error: ChartError) -> None:
        super().__init__(error.message)
        self.error = error
