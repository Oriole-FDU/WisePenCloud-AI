from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

MatrixEntry = StrictInt | StrictFloat | StrictStr


class RejectExplicitNullMixin:
    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        null_keys = sorted(key for key, value in data.items() if value is None)
        if null_keys:
            raise ValueError(f"arguments must not be null: {', '.join(null_keys)}")

        return data


def _normalize_bound_aliases(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(data)
    if normalized.get("task") == "definite_integral":
        if "lower_bound" not in normalized and "lower" in normalized:
            normalized["lower_bound"] = normalized.pop("lower")
        if "upper_bound" not in normalized and "upper" in normalized:
            normalized["upper_bound"] = normalized.pop("upper")
    return normalized


class SageComputeRequest(RejectExplicitNullMixin, BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task: StrictStr

    # Generic symbolic input
    expression: Optional[StrictStr] = None
    variable: Optional[StrictStr] = None
    point: Optional[StrictStr] = None
    order: Optional[StrictInt] = None
    lower_bound: Optional[StrictStr] = None
    upper_bound: Optional[StrictStr] = None
    substitutions: Optional[Dict[StrictStr, StrictStr]] = None

    # Equation solving
    equation: Optional[StrictStr] = None
    equations: Optional[List[StrictStr]] = None
    variables: Optional[List[StrictStr]] = None

    # Matrix / linear algebra
    matrix: Optional[List[List[MatrixEntry]]] = None
    vector: Optional[List[MatrixEntry]] = None
    matrix_power: Optional[StrictInt] = None
    row_index: Optional[StrictInt] = None
    column_index: Optional[StrictInt] = None
    ring: Optional[StrictStr] = None

    # Number theory
    integer: Optional[StrictInt] = None
    integers: Optional[List[StrictInt]] = None
    base: Optional[StrictInt] = None
    exponent: Optional[StrictInt] = None
    modulus: Optional[StrictInt] = None
    residues: Optional[List[StrictInt]] = None
    moduli: Optional[List[StrictInt]] = None
    n: Optional[StrictInt] = None
    k: Optional[StrictInt] = None

    # Polynomial / finite field
    polynomial: Optional[StrictStr] = None
    polynomial_a: Optional[StrictStr] = None
    polynomial_b: Optional[StrictStr] = None
    field: Optional[StrictStr] = None
    evaluate_at: Optional[StrictStr] = None

    # Finite field operation
    operation: Optional[StrictStr] = None
    element: Optional[StrictStr] = None
    element_a: Optional[StrictStr] = None
    element_b: Optional[StrictStr] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _normalize_bound_aliases(data)
        return data


class SageComputeResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    status: StrictStr
    task: StrictStr
    exact_result: Optional[StrictStr] = None
    numeric_result: Optional[StrictStr] = None
    latex_result: Optional[StrictStr] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[StrictStr] = Field(default_factory=list)
    error: Optional[StrictStr] = None
