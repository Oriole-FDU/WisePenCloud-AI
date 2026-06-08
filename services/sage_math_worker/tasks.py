import re
from functools import reduce
from typing import Any, Callable, Dict, List, Optional

from sage.all import (
    GF,
    QQ,
    SR,
    ZZ,
    CRT_list,
    Integer,
    PolynomialRing,
    binomial,
    divisors,
    euler_phi,
    expand,
    factor,
    factorial,
    gcd,
    inverse_mod,
    latex,
    lcm,
    matrix,
    moebius,
    next_prime,
    power_mod,
    sigma,
    solve,
    var,
    vector,
    xgcd,
)
from sage.all import is_prime as sage_is_prime

from models import SageComputeRequest, SageComputeResponse

_SUPPORTED_TASKS = {
    # Symbolic algebra
    "symbolic_simplify",
    "symbolic_expand",
    "symbolic_factor",
    "symbolic_collect",
    "symbolic_partial_fraction",
    "symbolic_numerator_denominator",
    "symbolic_substitute",

    # Calculus
    "differentiate",
    "integrate",
    "definite_integral",
    "limit",
    "taylor_series",

    # Equation solving
    "solve_equation",
    "solve_system",

    # Linear algebra
    "matrix_determinant",
    "matrix_trace",
    "matrix_characteristic_polynomial",
    "matrix_minimal_polynomial",
    "matrix_power",
    "matrix_power_entry",
    "matrix_inverse",
    "matrix_rref",
    "matrix_rank",
    "matrix_eigenvalues",
    "matrix_eigenvectors",
    "matrix_kernel",
    "matrix_image",
    "matrix_transpose",
    "matrix_solve",
    "matrix_smith_form",
    "matrix_hermite_form",

    # Number theory
    "modular_arithmetic",
    "modular_inverse",
    "gcd",
    "lcm",
    "xgcd",
    "prime_factorization",
    "is_prime",
    "next_prime",
    "euler_phi",
    "divisors",
    "sigma",
    "moebius",
    "crt",

    # Polynomial / finite field / exact polynomial algebra
    "polynomial_factor",
    "polynomial_expand",
    "polynomial_derivative",
    "polynomial_integral",
    "polynomial_resultant",
    "polynomial_gcd",
    "polynomial_lcm",
    "polynomial_discriminant",
    "polynomial_roots",
    "polynomial_degree",
    "polynomial_coefficients",
    "polynomial_evaluate",
    "polynomial_quotient_remainder",
    "polynomial_squarefree_decomposition",
    "polynomial_factor_over_field",
    "polynomial_roots_over_field",
    "polynomial_gcd_over_field",
    "polynomial_is_irreducible_over_field",

    # Finite field
    "finite_field_basic",
    "finite_field_operation",

    # Combinatorics
    "binomial",
    "factorial",
}

_FIELD_PATTERN = re.compile(r"^GF\((\d+)\)$")
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPRESSION_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9_+\-*/^().,=\s]+$")
_POLYNOMIAL_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9_+\-*/^().\s]+$")
_ELEMENT_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9_+\-*/^().\s]+$")


def compute_sage(request: SageComputeRequest) -> SageComputeResponse:
    handlers: Dict[str, Callable[[SageComputeRequest], SageComputeResponse]] = {
        "symbolic_simplify": _symbolic_simplify,
        "symbolic_expand": _symbolic_expand,
        "symbolic_factor": _symbolic_factor,
        "symbolic_collect": _symbolic_collect,
        "symbolic_partial_fraction": _symbolic_partial_fraction,
        "symbolic_numerator_denominator": _symbolic_numerator_denominator,
        "symbolic_substitute": _symbolic_substitute,
        "differentiate": _differentiate,
        "integrate": _integrate,
        "definite_integral": _definite_integral,
        "limit": _limit,
        "taylor_series": _taylor_series,
        "solve_equation": _solve_equation,
        "solve_system": _solve_system,
        "matrix_determinant": _matrix_determinant,
        "matrix_trace": _matrix_trace,
        "matrix_characteristic_polynomial": _matrix_characteristic_polynomial,
        "matrix_minimal_polynomial": _matrix_minimal_polynomial,
        "matrix_power": _matrix_power,
        "matrix_power_entry": _matrix_power_entry,
        "matrix_inverse": _matrix_inverse,
        "matrix_rref": _matrix_rref,
        "matrix_rank": _matrix_rank,
        "matrix_eigenvalues": _matrix_eigenvalues,
        "matrix_eigenvectors": _matrix_eigenvectors,
        "matrix_kernel": _matrix_kernel,
        "matrix_image": _matrix_image,
        "matrix_transpose": _matrix_transpose,
        "matrix_solve": _matrix_solve,
        "matrix_smith_form": _matrix_smith_form,
        "matrix_hermite_form": _matrix_hermite_form,
        "modular_arithmetic": _modular_arithmetic,
        "modular_inverse": _modular_inverse,
        "gcd": _gcd,
        "lcm": _lcm,
        "xgcd": _xgcd,
        "prime_factorization": _prime_factorization,
        "is_prime": _is_prime,
        "next_prime": _next_prime,
        "euler_phi": _euler_phi,
        "divisors": _divisors,
        "sigma": _sigma,
        "moebius": _moebius,
        "crt": _crt,
        "polynomial_factor": _polynomial_factor,
        "polynomial_expand": _polynomial_expand,
        "polynomial_derivative": _polynomial_derivative,
        "polynomial_integral": _polynomial_integral,
        "polynomial_resultant": _polynomial_resultant,
        "polynomial_gcd": _polynomial_gcd,
        "polynomial_lcm": _polynomial_lcm,
        "polynomial_discriminant": _polynomial_discriminant,
        "polynomial_roots": _polynomial_roots,
        "polynomial_degree": _polynomial_degree,
        "polynomial_coefficients": _polynomial_coefficients,
        "polynomial_evaluate": _polynomial_evaluate,
        "polynomial_quotient_remainder": _polynomial_quotient_remainder,
        "polynomial_squarefree_decomposition": _polynomial_squarefree_decomposition,
        "polynomial_factor_over_field": _polynomial_factor_over_field,
        "polynomial_roots_over_field": _polynomial_roots_over_field,
        "polynomial_gcd_over_field": _polynomial_gcd_over_field,
        "polynomial_is_irreducible_over_field": _polynomial_is_irreducible_over_field,
        "finite_field_basic": _finite_field_basic,
        "finite_field_operation": _finite_field_operation,
        "binomial": _binomial,
        "factorial": _factorial,
    }

    if request.task not in handlers:
        return SageComputeResponse(
            status="error",
            task=request.task,
            error=f"Unsupported Sage task: {request.task}.",
            metadata={"supported_tasks": sorted(_SUPPORTED_TASKS)},
        )

    try:
        return handlers[request.task](request)
    except Exception as e:
        return SageComputeResponse(
            status="error",
            task=request.task,
            error=f"Sage task failed: {e}",
        )


def _ok(
    request: SageComputeRequest,
    result: Any,
    numeric_result: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
) -> SageComputeResponse:
    return SageComputeResponse(
        status="ok",
        task=request.task,
        exact_result=str(result),
        numeric_result=str(numeric_result) if numeric_result is not None else None,
        latex_result=str(latex(result)),
        metadata=metadata or {},
        warnings=warnings or [],
    )


def _error(request: SageComputeRequest, message: str) -> SageComputeResponse:
    return SageComputeResponse(
        status="error",
        task=request.task,
        error=message,
    )


def _require_expression(request: SageComputeRequest) -> str:
    if request.expression is None:
        raise ValueError(f"{request.task} requires expression.")
    return request.expression


def _require_variable(request: SageComputeRequest) -> str:
    variable = request.variable or "x"
    if not _SYMBOL_PATTERN.match(variable):
        raise ValueError("variable must be a simple symbol name.")
    return variable


def _normalize_power_syntax(text: str) -> str:
    return text.replace("^", "**")


def _parse_symbolic_expression(expression: str):
    if not _EXPRESSION_ALLOWED_PATTERN.match(expression):
        raise ValueError("expression contains unsupported characters.")
    return SR(_normalize_power_syntax(expression))


def _parse_point(point: str):
    if not _EXPRESSION_ALLOWED_PATTERN.match(point):
        raise ValueError("point contains unsupported characters.")
    return SR(_normalize_power_syntax(point))


def _parse_equation(equation: str):
    if "=" not in equation:
        raise ValueError("equation must contain '='.")

    left, right = equation.split("=", 1)
    return _parse_symbolic_expression(left) == _parse_symbolic_expression(right)


def _parse_ring(ring_text: Optional[str] = None):
    if ring_text is None:
        return QQ

    if ring_text == "ZZ":
        return ZZ

    if ring_text == "QQ":
        return QQ

    field_match = _FIELD_PATTERN.match(ring_text)
    if field_match:
        q = int(field_match.group(1))
        if q <= 1:
            raise ValueError("finite field order must be greater than 1.")
        return GF(q, "a")

    raise ValueError("ring must be ZZ, QQ, or GF(q).")


def _parse_matrix(rows: Optional[List[List[Any]]], ring_text: Optional[str] = None):
    if rows is None:
        raise ValueError("matrix is required.")

    if len(rows) == 0:
        raise ValueError("matrix must not be empty.")

    if not all(isinstance(row, list) for row in rows):
        raise ValueError("matrix must be a list of rows.")

    width = len(rows[0])
    if width == 0:
        raise ValueError("matrix rows must not be empty.")

    if any(len(row) != width for row in rows):
        raise ValueError("matrix rows must have the same length.")

    ring = _parse_ring(ring_text)
    parsed_rows = []

    for row in rows:
        parsed_rows.append([_parse_ring_entry(ring, item) for item in row])

    return matrix(ring, parsed_rows)


def _parse_vector(values: Optional[List[Any]], ring_text: Optional[str] = None):
    if values is None:
        raise ValueError("vector is required.")

    if len(values) == 0:
        raise ValueError("vector must not be empty.")

    ring = _parse_ring(ring_text)
    return vector(ring, [_parse_ring_entry(ring, item) for item in values])


def _parse_ring_entry(ring, value: Any):
    if isinstance(value, bool):
        raise ValueError("matrix entries must not be boolean values.")
    if isinstance(value, int):
        return ring(value)
    if isinstance(value, float):
        return ring(value)
    if isinstance(value, str):
        return ring(_normalize_power_syntax(value))
    raise ValueError("matrix entries must be integers, floats, or expression strings.")


def _parse_integers(values: Optional[List[int]]) -> List[Integer]:
    if values is None:
        raise ValueError("integers is required.")

    if len(values) == 0:
        raise ValueError("integers must not be empty.")

    return [Integer(value) for value in values]


def _parse_polynomial_ring(variable_name: Optional[str] = None, ring_text: Optional[str] = None):
    variable = variable_name or "x"
    if not _SYMBOL_PATTERN.match(variable):
        raise ValueError("variable must be a simple symbol name.")

    base_ring = _parse_ring(ring_text)
    return PolynomialRing(base_ring, variable)


def _parse_polynomial(
    polynomial_text: str,
    variable_name: Optional[str] = None,
    ring_text: Optional[str] = None,
):
    if not _POLYNOMIAL_ALLOWED_PATTERN.match(polynomial_text):
        raise ValueError("polynomial contains unsupported characters.")

    ring = _parse_polynomial_ring(variable_name, ring_text)
    return ring(_normalize_power_syntax(polynomial_text))


def _parse_polynomial_over_field(
    polynomial_text: str,
    field_text: str,
    variable_name: Optional[str] = None,
):
    return _parse_polynomial(polynomial_text, variable_name, field_text)


def _parse_field_element(field, element_text: str):
    if not _ELEMENT_ALLOWED_PATTERN.match(element_text):
        raise ValueError("field element contains unsupported characters.")
    return field(_normalize_power_syntax(element_text))


def _parse_integer_text(value: str):
    if not re.fullmatch(r"[+-]?\d+", value):
        raise ValueError("exponent must be an integer string.")
    return Integer(value)


# ---------------------------------------------------------------------
# Symbolic algebra
# ---------------------------------------------------------------------


def _symbolic_simplify(request: SageComputeRequest) -> SageComputeResponse:
    expr = _parse_symbolic_expression(_require_expression(request))
    result = expr.simplify_full()
    return _ok(request, result)


def _symbolic_expand(request: SageComputeRequest) -> SageComputeResponse:
    expr = _parse_symbolic_expression(_require_expression(request))
    result = expand(expr)
    return _ok(request, result)


def _symbolic_factor(request: SageComputeRequest) -> SageComputeResponse:
    expr = _parse_symbolic_expression(_require_expression(request))
    result = factor(expr)
    return _ok(request, result)


def _symbolic_collect(request: SageComputeRequest) -> SageComputeResponse:
    variable_name = _require_variable(request)
    symbol = var(variable_name)
    expr = _parse_symbolic_expression(_require_expression(request))
    result = expr.collect(symbol)
    return _ok(request, result)


def _symbolic_partial_fraction(request: SageComputeRequest) -> SageComputeResponse:
    variable_name = _require_variable(request)
    symbol = var(variable_name)
    expr = _parse_symbolic_expression(_require_expression(request))
    result = expr.partial_fraction(symbol)
    return _ok(request, result)


def _symbolic_numerator_denominator(request: SageComputeRequest) -> SageComputeResponse:
    expr = _parse_symbolic_expression(_require_expression(request))
    result = expr.numerator_denominator()
    return _ok(request, result)


def _symbolic_substitute(request: SageComputeRequest) -> SageComputeResponse:
    if request.substitutions is None:
        return _error(request, "symbolic_substitute requires substitutions.")

    expr = _parse_symbolic_expression(_require_expression(request))
    substitution_map = {}

    for name, value in request.substitutions.items():
        if not _SYMBOL_PATTERN.match(name):
            return _error(request, f"invalid substitution variable: {name}")
        substitution_map[var(name)] = _parse_symbolic_expression(value)

    result = expr.subs(substitution_map)
    return _ok(request, result)


# ---------------------------------------------------------------------
# Calculus
# ---------------------------------------------------------------------


def _differentiate(request: SageComputeRequest) -> SageComputeResponse:
    variable_name = _require_variable(request)
    symbol = var(variable_name)
    expr = _parse_symbolic_expression(_require_expression(request))
    result = expr.diff(symbol)
    return _ok(request, result)


def _integrate(request: SageComputeRequest) -> SageComputeResponse:
    variable_name = _require_variable(request)
    symbol = var(variable_name)
    expr = _parse_symbolic_expression(_require_expression(request))
    result = expr.integrate(symbol)
    return _ok(request, result)


def _definite_integral(request: SageComputeRequest) -> SageComputeResponse:
    if request.lower_bound is None or request.upper_bound is None:
        return _error(request, "definite_integral requires lower_bound and upper_bound.")

    variable_name = _require_variable(request)
    symbol = var(variable_name)
    expr = _parse_symbolic_expression(_require_expression(request))
    lower = _parse_symbolic_expression(request.lower_bound)
    upper = _parse_symbolic_expression(request.upper_bound)

    result = expr.integrate(symbol, lower, upper)
    return _ok(request, result, numeric_result=result.n())


def _limit(request: SageComputeRequest) -> SageComputeResponse:
    if request.point is None:
        return _error(request, "limit requires point.")

    variable_name = _require_variable(request)
    expr = _parse_symbolic_expression(_require_expression(request))
    point = _parse_point(request.point)

    result = expr.limit(**{variable_name: point})
    return _ok(request, result)


def _taylor_series(request: SageComputeRequest) -> SageComputeResponse:
    if request.point is None:
        return _error(request, "taylor_series requires point.")

    if request.order is None:
        return _error(request, "taylor_series requires order.")

    if request.order <= 0:
        return _error(request, "order must be positive.")

    variable_name = _require_variable(request)
    symbol = var(variable_name)
    expr = _parse_symbolic_expression(_require_expression(request))
    point = _parse_point(request.point)

    result = expr.taylor(symbol, point, request.order)
    return _ok(request, result, metadata={"order": request.order})


# ---------------------------------------------------------------------
# Equation solving
# ---------------------------------------------------------------------


def _solve_equation(request: SageComputeRequest) -> SageComputeResponse:
    if request.equation is None:
        return _error(request, "solve_equation requires equation.")

    variable_name = _require_variable(request)
    symbol = var(variable_name)
    equation = _parse_equation(request.equation)

    result = solve(equation, symbol)
    return _ok(request, result)


def _solve_system(request: SageComputeRequest) -> SageComputeResponse:
    if request.equations is None:
        return _error(request, "solve_system requires equations.")

    if request.variables is None:
        return _error(request, "solve_system requires variables.")

    symbols = []
    for variable_name in request.variables:
        if not _SYMBOL_PATTERN.match(variable_name):
            return _error(request, f"invalid variable: {variable_name}")
        symbols.append(var(variable_name))

    equations = [_parse_equation(equation) for equation in request.equations]
    result = solve(equations, symbols)
    return _ok(request, result)


# ---------------------------------------------------------------------
# Linear algebra
# ---------------------------------------------------------------------


def _matrix_determinant(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.det()
    return _ok(request, result)


def _matrix_trace(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.trace()
    return _ok(request, result)


def _matrix_characteristic_polynomial(request: SageComputeRequest) -> SageComputeResponse:
    variable_name = _require_variable(request)
    mat = _parse_matrix(request.matrix, request.ring or "ZZ")
    result = mat.charpoly(variable_name)
    return _ok(request, result)


def _matrix_minimal_polynomial(request: SageComputeRequest) -> SageComputeResponse:
    variable_name = _require_variable(request)
    mat = _parse_matrix(request.matrix, request.ring or "ZZ")

    if hasattr(mat, "minpoly"):
        result = mat.minpoly(variable_name)
    elif hasattr(mat, "minimal_polynomial"):
        result = mat.minimal_polynomial()
    else:
        return _error(request, "matrix minimal polynomial is not available for this matrix type.")

    return _ok(request, result)


def _matrix_power(request: SageComputeRequest) -> SageComputeResponse:
    if request.matrix_power is None:
        return _error(request, "matrix_power requires matrix_power.")

    if request.matrix_power < 0:
        return _error(request, "matrix_power must be non-negative.")

    mat = _parse_matrix(request.matrix, request.ring or "ZZ")
    result = mat ** request.matrix_power
    return _ok(request, result, metadata={"matrix_power": request.matrix_power})


def _matrix_power_entry(request: SageComputeRequest) -> SageComputeResponse:
    if request.matrix_power is None:
        return _error(request, "matrix_power_entry requires matrix_power.")

    if request.row_index is None or request.column_index is None:
        return _error(request, "matrix_power_entry requires row_index and column_index.")

    if request.matrix_power < 0:
        return _error(request, "matrix_power must be non-negative.")

    if request.row_index < 0 or request.column_index < 0:
        return _error(request, "row_index and column_index must be non-negative.")

    mat = _parse_matrix(request.matrix, request.ring or "ZZ")

    if request.row_index >= mat.nrows() or request.column_index >= mat.ncols():
        return _error(request, "row_index or column_index is out of matrix bounds.")

    powered = mat ** request.matrix_power
    result = powered[request.row_index, request.column_index]

    return _ok(
        request,
        result,
        numeric_result=result,
        metadata={
            "matrix_power": request.matrix_power,
            "row_index": request.row_index,
            "column_index": request.column_index,
        },
    )


def _matrix_inverse(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.inverse()
    return _ok(request, result)


def _matrix_rref(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.rref()
    return _ok(request, result)


def _matrix_rank(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.rank()
    return _ok(request, result)


def _matrix_eigenvalues(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.eigenvalues()
    return _ok(request, result)


def _matrix_eigenvectors(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.eigenvectors_right()
    return _ok(request, result)


def _matrix_kernel(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.right_kernel()
    return _ok(request, result)


def _matrix_image(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.column_space()
    return _ok(request, result)


def _matrix_transpose(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    result = mat.transpose()
    return _ok(request, result)


def _matrix_solve(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, request.ring)
    vec = _parse_vector(request.vector, request.ring)
    result = mat.solve_right(vec)
    return _ok(request, result)


def _matrix_smith_form(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, "ZZ")
    result = mat.smith_form()
    return _ok(request, result)


def _matrix_hermite_form(request: SageComputeRequest) -> SageComputeResponse:
    mat = _parse_matrix(request.matrix, "ZZ")
    result = mat.hermite_form()
    return _ok(request, result)


# ---------------------------------------------------------------------
# Number theory
# ---------------------------------------------------------------------


def _modular_arithmetic(request: SageComputeRequest) -> SageComputeResponse:
    if request.base is None or request.exponent is None or request.modulus is None:
        return _error(request, "modular_arithmetic requires base, exponent, and modulus.")

    if request.modulus <= 0:
        return _error(request, "modulus must be positive.")

    result = power_mod(
        Integer(request.base),
        Integer(request.exponent),
        Integer(request.modulus),
    )
    return _ok(request, result, numeric_result=result)


def _modular_inverse(request: SageComputeRequest) -> SageComputeResponse:
    if request.base is None or request.modulus is None:
        return _error(request, "modular_inverse requires base and modulus.")

    if request.modulus <= 1:
        return _error(request, "modulus must be greater than 1.")

    result = inverse_mod(Integer(request.base), Integer(request.modulus))
    return _ok(request, result, numeric_result=result)


def _gcd(request: SageComputeRequest) -> SageComputeResponse:
    integers = _parse_integers(request.integers)
    result = reduce(gcd, integers)
    return _ok(request, result, numeric_result=result)


def _lcm(request: SageComputeRequest) -> SageComputeResponse:
    integers = _parse_integers(request.integers)
    result = reduce(lcm, integers)
    return _ok(request, result, numeric_result=result)


def _xgcd(request: SageComputeRequest) -> SageComputeResponse:
    integers = _parse_integers(request.integers)

    if len(integers) != 2:
        return _error(request, "xgcd requires exactly two integers.")

    result = xgcd(integers[0], integers[1])
    return _ok(request, result)


def _prime_factorization(request: SageComputeRequest) -> SageComputeResponse:
    if request.integer is None:
        return _error(request, "prime_factorization requires integer.")

    result = factor(Integer(request.integer))
    return _ok(request, result)


def _is_prime(request: SageComputeRequest) -> SageComputeResponse:
    if request.integer is None:
        return _error(request, "is_prime requires integer.")

    result = bool(sage_is_prime(Integer(request.integer)))
    result_text = "true" if result else "false"

    return SageComputeResponse(
        status="ok",
        task=request.task,
        exact_result=result_text,
        numeric_result=result_text,
        latex_result=result_text,
    )


def _next_prime(request: SageComputeRequest) -> SageComputeResponse:
    if request.integer is None:
        return _error(request, "next_prime requires integer.")

    result = next_prime(Integer(request.integer))
    return _ok(request, result, numeric_result=result)


def _euler_phi(request: SageComputeRequest) -> SageComputeResponse:
    if request.integer is None:
        return _error(request, "euler_phi requires integer.")

    result = euler_phi(Integer(request.integer))
    return _ok(request, result, numeric_result=result)


def _divisors(request: SageComputeRequest) -> SageComputeResponse:
    if request.integer is None:
        return _error(request, "divisors requires integer.")

    result = divisors(Integer(request.integer))
    return _ok(request, result)


def _sigma(request: SageComputeRequest) -> SageComputeResponse:
    if request.integer is None:
        return _error(request, "sigma requires integer.")

    result = sigma(Integer(request.integer))
    return _ok(request, result, numeric_result=result)


def _moebius(request: SageComputeRequest) -> SageComputeResponse:
    if request.integer is None:
        return _error(request, "moebius requires integer.")

    result = moebius(Integer(request.integer))
    return _ok(request, result, numeric_result=result)


def _crt(request: SageComputeRequest) -> SageComputeResponse:
    if request.residues is None or request.moduli is None:
        return _error(request, "crt requires residues and moduli.")

    if len(request.residues) != len(request.moduli):
        return _error(request, "residues and moduli must have the same length.")

    residues = [Integer(value) for value in request.residues]
    moduli = [Integer(value) for value in request.moduli]

    result = CRT_list(residues, moduli)
    return _ok(request, result, numeric_result=result)


# ---------------------------------------------------------------------
# Polynomial / finite field / exact polynomial algebra
# ---------------------------------------------------------------------


def _polynomial_factor(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_factor requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.factor()
    return _ok(request, result)


def _polynomial_expand(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_expand requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.expand()
    return _ok(request, result)


def _polynomial_derivative(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_derivative requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.derivative()
    return _ok(request, result)


def _polynomial_integral(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_integral requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.integral()
    return _ok(request, result)


def _polynomial_resultant(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial_a is None or request.polynomial_b is None:
        return _error(request, "polynomial_resultant requires polynomial_a and polynomial_b.")

    polynomial_a = _parse_polynomial(request.polynomial_a, request.variable, request.ring or "QQ")
    polynomial_b = _parse_polynomial(request.polynomial_b, request.variable, request.ring or "QQ")

    result = polynomial_a.resultant(polynomial_b)
    return _ok(request, result, numeric_result=result)


def _polynomial_gcd(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial_a is None or request.polynomial_b is None:
        return _error(request, "polynomial_gcd requires polynomial_a and polynomial_b.")

    polynomial_a = _parse_polynomial(request.polynomial_a, request.variable, request.ring or "QQ")
    polynomial_b = _parse_polynomial(request.polynomial_b, request.variable, request.ring or "QQ")

    result = polynomial_a.gcd(polynomial_b)
    return _ok(request, result)


def _polynomial_lcm(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial_a is None or request.polynomial_b is None:
        return _error(request, "polynomial_lcm requires polynomial_a and polynomial_b.")

    polynomial_a = _parse_polynomial(request.polynomial_a, request.variable, request.ring or "QQ")
    polynomial_b = _parse_polynomial(request.polynomial_b, request.variable, request.ring or "QQ")

    result = polynomial_a.lcm(polynomial_b)
    return _ok(request, result)


def _polynomial_discriminant(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_discriminant requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.discriminant()

    return _ok(request, result, numeric_result=result)


def _polynomial_roots(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_roots requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.roots(multiplicities=True)
    return _ok(request, result)


def _polynomial_degree(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_degree requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.degree()
    return _ok(request, result, numeric_result=result)


def _polynomial_coefficients(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_coefficients requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.coefficients(sparse=False)
    return _ok(request, result)


def _polynomial_evaluate(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_evaluate requires polynomial.")

    if request.evaluate_at is None:
        return _error(request, "polynomial_evaluate requires evaluate_at.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    ring = polynomial.parent().base_ring()
    value = ring(_normalize_power_syntax(request.evaluate_at))
    result = polynomial(value)
    return _ok(request, result, numeric_result=result)


def _polynomial_quotient_remainder(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial_a is None or request.polynomial_b is None:
        return _error(request, "polynomial_quotient_remainder requires polynomial_a and polynomial_b.")

    polynomial_a = _parse_polynomial(request.polynomial_a, request.variable, request.ring or "QQ")
    polynomial_b = _parse_polynomial(request.polynomial_b, request.variable, request.ring or "QQ")

    quotient, remainder = polynomial_a.quo_rem(polynomial_b)
    result = {"quotient": str(quotient), "remainder": str(remainder)}

    return SageComputeResponse(
        status="ok",
        task=request.task,
        exact_result=str(result),
        latex_result=str(result),
        metadata=result,
    )


def _polynomial_squarefree_decomposition(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None:
        return _error(request, "polynomial_squarefree_decomposition requires polynomial.")

    polynomial = _parse_polynomial(request.polynomial, request.variable, request.ring or "QQ")
    result = polynomial.squarefree_decomposition()
    return _ok(request, result)


def _polynomial_factor_over_field(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None or request.field is None:
        return _error(request, "polynomial_factor_over_field requires polynomial and field.")

    polynomial = _parse_polynomial_over_field(request.polynomial, request.field, request.variable)
    result = polynomial.factor()

    return _ok(request, result)


def _polynomial_roots_over_field(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None or request.field is None:
        return _error(request, "polynomial_roots_over_field requires polynomial and field.")

    polynomial = _parse_polynomial_over_field(request.polynomial, request.field, request.variable)
    result = polynomial.roots(multiplicities=True)

    return _ok(request, result)


def _polynomial_gcd_over_field(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial_a is None or request.polynomial_b is None or request.field is None:
        return _error(request, "polynomial_gcd_over_field requires polynomial_a, polynomial_b, and field.")

    polynomial_a = _parse_polynomial_over_field(request.polynomial_a, request.field, request.variable)
    polynomial_b = _parse_polynomial_over_field(request.polynomial_b, request.field, request.variable)

    result = polynomial_a.gcd(polynomial_b)
    return _ok(request, result)


def _polynomial_is_irreducible_over_field(request: SageComputeRequest) -> SageComputeResponse:
    if request.polynomial is None or request.field is None:
        return _error(request, "polynomial_is_irreducible_over_field requires polynomial and field.")

    polynomial = _parse_polynomial_over_field(request.polynomial, request.field, request.variable)
    result = bool(polynomial.is_irreducible())
    result_text = "true" if result else "false"

    return SageComputeResponse(
        status="ok",
        task=request.task,
        exact_result=result_text,
        numeric_result=result_text,
        latex_result=result_text,
    )


# ---------------------------------------------------------------------
# Finite field
# ---------------------------------------------------------------------


def _finite_field_basic(request: SageComputeRequest) -> SageComputeResponse:
    if request.field is None:
        return _error(request, "finite_field_basic requires field.")

    field = _parse_ring(request.field)

    result = {
        "field": str(field),
        "order": int(field.order()),
        "characteristic": int(field.characteristic()),
    }

    return SageComputeResponse(
        status="ok",
        task=request.task,
        exact_result=str(result),
        latex_result=str(field),
        metadata=result,
    )


def _finite_field_operation(request: SageComputeRequest) -> SageComputeResponse:
    if request.field is None:
        return _error(request, "finite_field_operation requires field.")

    if request.operation is None:
        return _error(request, "finite_field_operation requires operation.")

    field = _parse_ring(request.field)
    operation = request.operation

    if operation == "neg":
        if request.element is None:
            return _error(request, "finite_field_operation neg requires element.")
        element = _parse_field_element(field, request.element)
        result = -element
        return _ok(request, result)

    if request.element_a is None or request.element_b is None:
        return _error(request, "finite_field_operation requires element_a and element_b.")

    if operation == "add":
        element_a = _parse_field_element(field, request.element_a)
        element_b = _parse_field_element(field, request.element_b)
        result = element_a + element_b
    elif operation == "sub":
        element_a = _parse_field_element(field, request.element_a)
        element_b = _parse_field_element(field, request.element_b)
        result = element_a - element_b
    elif operation == "mul":
        element_a = _parse_field_element(field, request.element_a)
        element_b = _parse_field_element(field, request.element_b)
        result = element_a * element_b
    elif operation == "div":
        element_a = _parse_field_element(field, request.element_a)
        element_b = _parse_field_element(field, request.element_b)
        result = element_a / element_b
    elif operation == "pow":
        element_a = _parse_field_element(field, request.element_a)
        result = element_a ** _parse_integer_text(request.element_b)
    else:
        return _error(request, "operation must be add, sub, mul, div, pow, or neg.")

    return _ok(request, result)


# ---------------------------------------------------------------------
# Combinatorics
# ---------------------------------------------------------------------


def _binomial(request: SageComputeRequest) -> SageComputeResponse:
    if request.n is None or request.k is None:
        return _error(request, "binomial requires n and k.")

    result = binomial(Integer(request.n), Integer(request.k))
    return _ok(request, result, numeric_result=result)


def _factorial(request: SageComputeRequest) -> SageComputeResponse:
    if request.n is None:
        return _error(request, "factorial requires n.")

    if request.n < 0:
        return _error(request, "factorial requires n >= 0.")

    result = factorial(Integer(request.n))
    return _ok(request, result, numeric_result=result)
