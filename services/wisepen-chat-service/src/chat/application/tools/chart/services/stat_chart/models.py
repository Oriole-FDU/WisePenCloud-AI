from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StatChartDataInput:
    records: list[dict[str, Any]]
    columns: list[str] | None = None


@dataclass(frozen=True)
class StatChartMapping:
    x: str | None = None
    y: str | None = None
    value: str | None = None
    hue: str | None = None
    style: str | None = None
    size: str | None = None
    row: str | None = None
    col: str | None = None

    def selected_columns(self) -> dict[str, str | None]:
        """返回非空 mapping 字段，用于统一做列名存在性检查。"""
        return {
            field_name: value
            for field_name, value in {
                "x": self.x,
                "y": self.y,
                "value": self.value,
                "hue": self.hue,
                "style": self.style,
                "size": self.size,
                "row": self.row,
                "col": self.col,
            }.items()
            if value
        }


@dataclass(frozen=True)
class StatChartOptions:
    bins: int | None = None
    kde: bool = False
    estimator: str = "mean"
    errorbar: str | None = "ci"
    confidence_level: int = 95
    regression_order: int = 1
    robust: bool = False
    lowess: bool = False
    log_x: bool = False
    log_y: bool = False
    show_data_labels: bool = False
    sort_categories: str = "none"
    annot: bool = False
    center_zero: bool = False


@dataclass(frozen=True)
class StatChartRequest:
    chart_kind: str
    data: StatChartDataInput
    mapping: StatChartMapping = field(default_factory=StatChartMapping)
    options: StatChartOptions = field(default_factory=StatChartOptions)
    title: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    output_formats: list[str] = field(default_factory=lambda: ["svg", "png"])
    style_profile: str = "academic_default"


@dataclass(frozen=True)
class StatChartArtifact:
    kind: str
    mime_type: str
    content: str
    encoding: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "mime_type": self.mime_type,
            "content": self.content,
            "encoding": self.encoding,
        }


@dataclass(frozen=True)
class StatChartResult:
    chart_id: str
    chart_kind: str
    artifacts: list[StatChartArtifact]
    columns: list[str]
    row_count: int
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_id": self.chart_id,
            "chart_kind": self.chart_kind,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "columns": self.columns,
            "row_count": self.row_count,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }
