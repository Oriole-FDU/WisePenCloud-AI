from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sympy as sp


@dataclass(frozen=True)
class FunctionPlotRequest:
    plot_kind: str = "line_2d"
    expressions: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=lambda: ["x"])
    x_range: tuple[float, float] = (-10.0, 10.0)
    y_range: tuple[float, float] | None = None
    samples: int = 1600
    output_formats: list[str] = field(default_factory=lambda: ["svg", "png"])
    style_profile: str = "academic_default"
    detect_roots: bool = False
    detect_extrema: bool = False
    title: str | None = None


@dataclass(frozen=True)
class ParsedExpression:
    raw: str
    normalized: str
    sympy_expr: sp.Expr
    latex: str
    variables: tuple[str, ...]


@dataclass
class LineSeries:
    expression: ParsedExpression
    x: np.ndarray
    y: np.ndarray
    warnings: list[str] = field(default_factory=list)


@dataclass
class GridSample:
    expression: ParsedExpression
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FunctionPlotFeature:
    kind: str
    expression: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    method: str = ""
    confidence: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "expression": self.expression,
            "method": self.method,
            "confidence": self.confidence,
        }
        if self.x is not None:
            payload["x"] = round(float(self.x), 10)
        if self.y is not None:
            payload["y"] = round(float(self.y), 10)
        if self.z is not None:
            payload["z"] = round(float(self.z), 10)
        return payload


@dataclass(frozen=True)
class FunctionPlotArtifact:
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
class FunctionPlotResult:
    plot_id: str
    plot_kind: str
    latex_expressions: list[str]
    artifacts: list[FunctionPlotArtifact]
    features: list[FunctionPlotFeature] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plot_id": self.plot_id,
            "plot_kind": self.plot_kind,
            "latex_expressions": self.latex_expressions,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "features": [feature.to_dict() for feature in self.features],
            "warnings": self.warnings,
            "metadata": self.metadata,
        }
