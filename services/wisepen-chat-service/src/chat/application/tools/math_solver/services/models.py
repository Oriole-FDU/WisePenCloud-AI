from dataclasses import dataclass, field
from typing import Any, List, Optional

from chat.application.tools.math_solver.services.sage_runtime.enums import SageMathTask


@dataclass(slots=True)
class MathSolverRequest:
    task: str

    expression: Optional[str] = None
    equation: Optional[str] = None
    equations: Optional[List[str]] = None
    variable: Optional[str] = None
    variables: Optional[List[str]] = None
    point: Optional[str] = None
    order: Optional[int] = None
    lower_bound: Optional[str] = None
    upper_bound: Optional[str] = None

    matrix: Optional[List[List[Any]]] = None
    matrix_b: Optional[List[List[Any]]] = None
    vector: Optional[List[Any]] = None

    n: Optional[int] = None
    k: Optional[int] = None
    probability: Optional[str] = None

    lower: Optional[str] = None
    upper: Optional[str] = None

    integer: Optional[int] = None
    integers: Optional[List[int]] = None
    base: Optional[int] = None
    exponent: Optional[int] = None
    modulus: Optional[int] = None
    residues: Optional[List[int]] = None
    moduli: Optional[List[int]] = None
    polynomial: Optional[str] = None
    polynomial_a: Optional[str] = None
    polynomial_b: Optional[str] = None
    field: Optional[str] = None
    ring: Optional[str] = None
    operation: Optional[str] = None
    element: Optional[str] = None
    element_a: Optional[str] = None
    element_b: Optional[str] = None


@dataclass(slots=True)
class MathSolverResult:
    task: str
    backend: str
    exact_result: Optional[Any] = None
    numeric_result: Optional[str] = None
    latex_result: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    is_formal_proof: bool = False


SAGE_MATH_TASKS = frozenset(task.value for task in SageMathTask)
