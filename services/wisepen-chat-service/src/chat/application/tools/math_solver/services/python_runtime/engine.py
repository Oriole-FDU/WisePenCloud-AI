from typing import Any

import sympy as sp
from scipy import optimize, stats

from chat.application.tools.math_solver.services.errors import MathSolverError
from chat.application.tools.math_solver.services.python_runtime.enums import PythonMathTask
from chat.application.tools.math_solver.services.python_runtime.expression_parser import (
    parse_math_expr,
)


class PythonMathEngine:

    @staticmethod
    def parse_variable(name: str | None, default: str = "x") -> sp.Symbol:
        return sp.Symbol(name or default)

    @staticmethod
    def parse_expression(expression: str | None, variables: list[str] | None = None) -> sp.Expr:
        return parse_math_expr(expression, variables)

    @staticmethod
    def parse_bound(value: str | None, name: str, variables: list[str] | None = None) -> sp.Expr:
        try:
            return PythonMathEngine.parse_expression(value, variables)
        except Exception as e:
            raise MathSolverError(f"{name} must be a non-empty math expression string.") from e

    # ---- symbolic ----

    def compute_symbolic(self, task: PythonMathTask, data: Any) -> Any:
        variable = self.parse_variable(data.variable)
        variable_names = data.variables or [str(variable)]

        if task == PythonMathTask.SIMPLIFY:
            return sp.simplify(self.parse_expression(data.expression, variable_names))
        if task == PythonMathTask.EXPAND:
            return sp.expand(self.parse_expression(data.expression, variable_names))
        if task == PythonMathTask.FACTOR:
            return sp.factor(self.parse_expression(data.expression, variable_names))
        if task == PythonMathTask.NUMERIC:
            return sp.N(self.parse_expression(data.expression, variable_names))
        if task == PythonMathTask.DIFFERENTIATE:
            return sp.diff(self.parse_expression(data.expression, variable_names), variable)
        if task == PythonMathTask.INTEGRATE:
            return sp.integrate(self.parse_expression(data.expression, variable_names), variable)
        if task == PythonMathTask.DEFINITE_INTEGRAL:
            if data.lower_bound is None or data.upper_bound is None:
                raise MathSolverError(
                    "definite_integral requires both lower_bound and upper_bound. "
                    "Use lower_bound/upper_bound for integration limits; lower/upper are only aliases.",
                    retryable=False,
                )
            lower = self.parse_bound(data.lower_bound, "lower_bound", variable_names)
            upper = self.parse_bound(data.upper_bound, "upper_bound", variable_names)
            return sp.integrate(
                self.parse_expression(data.expression, variable_names),
                (variable, lower, upper),
            )
        if task == PythonMathTask.LIMIT:
            point = self.parse_bound(data.point, "point", variable_names)
            return sp.limit(self.parse_expression(data.expression, variable_names), variable, point)
        if task == PythonMathTask.TAYLOR_SERIES:
            point = self.parse_bound(data.point, "point", variable_names)
            return sp.series(
                self.parse_expression(data.expression, variable_names),
                variable,
                point,
                data.order,
            )
        if task == PythonMathTask.SOLVE_EQUATION:
            left, right = data.equation.split("=", 1)
            equation = sp.Eq(self.parse_expression(left, variable_names), self.parse_expression(right, variable_names))
            return sp.solve(equation, variable)
        if task == PythonMathTask.SOLVE_SYSTEM:
            symbols = [sp.Symbol(name) for name in (data.variables or ["x"])]
            equations = []
            for eq in data.equations:
                left, right = eq.split("=", 1)
                equations.append(sp.Eq(self.parse_expression(left, data.variables), self.parse_expression(right, data.variables)))
            return sp.solve(equations, symbols, dict=True)
        if task == PythonMathTask.SUMMATION:
            lower = self.parse_bound(data.lower, "lower", variable_names)
            upper = self.parse_bound(data.upper, "upper", variable_names)
            return sp.summation(
                self.parse_expression(data.expression, variable_names),
                (variable, lower, upper),
            )

        raise ValueError(f"unsupported python math task: {task}")

    # ---- matrix ----

    @staticmethod
    def _parse_matrix(value: list[list[Any]] | None, name: str) -> sp.Matrix:
        return sp.Matrix([[sp.sympify(item) for item in row] for row in value])

    @staticmethod
    def _parse_vector(value: list[Any] | None, name: str) -> sp.Matrix:
        return sp.Matrix([sp.sympify(item) for item in value])

    def compute_matrix(self, task: PythonMathTask, data: Any) -> Any:
        matrix = self._parse_matrix(data.matrix, "matrix")

        if task == PythonMathTask.MATRIX_DETERMINANT:
            return matrix.det()
        if task == PythonMathTask.MATRIX_TRACE:
            return matrix.trace()
        if task == PythonMathTask.MATRIX_RANK:
            return matrix.rank()
        if task == PythonMathTask.MATRIX_INVERSE:
            return matrix.inv()
        if task == PythonMathTask.MATRIX_RREF:
            reduced, pivots = matrix.rref()
            return {"matrix": reduced, "pivots": list(pivots)}
        if task == PythonMathTask.MATRIX_EIGENVALUES:
            return matrix.eigenvals()
        if task == PythonMathTask.MATRIX_MULTIPLY:
            return matrix * self._parse_matrix(data.matrix_b, "matrix_b")
        if task == PythonMathTask.MATRIX_SOLVE:
            if data.vector is not None:
                rhs = self._parse_vector(data.vector, "vector")
            else:
                rhs = self._parse_matrix(data.matrix_b, "matrix_b")
            return matrix.gauss_jordan_solve(rhs)[0]

        raise ValueError(f"unsupported matrix task: {task}")

    # ---- probability / combinatorics ----

    def compute_combinatorics_or_probability(self, task: PythonMathTask, data: Any) -> Any:
        if task == PythonMathTask.FACTORIAL:
            return sp.factorial(data.n)
        if task == PythonMathTask.BINOMIAL:
            return sp.binomial(data.n, data.k)
        if task == PythonMathTask.PERMUTATION:
            return sp.factorial(data.n) / sp.factorial(data.n - data.k)
        if task == PythonMathTask.BINOMIAL_PROBABILITY:
            probability = self.parse_expression(data.probability, [])
            return sp.binomial(data.n, data.k) * probability ** data.k * (1 - probability) ** (data.n - data.k)
        if task == PythonMathTask.EXPECTATION:
            return self._finite_uniform_moment(data, power=1)
        if task == PythonMathTask.VARIANCE:
            mean = self._finite_uniform_moment(data, power=1)
            second = self._finite_uniform_moment(data, power=2)
            return sp.simplify(second - mean ** 2)

        raise ValueError(f"unsupported probability task: {task}")

    @staticmethod
    def _finite_uniform_moment(data: Any, *, power: int) -> sp.Expr:
        variable = PythonMathEngine.parse_variable(data.variable)
        variable_name = str(variable)
        lower = int(PythonMathEngine.parse_bound(data.lower, "lower", [variable_name]))
        upper = int(PythonMathEngine.parse_bound(data.upper, "upper", [variable_name]))
        expression = PythonMathEngine.parse_expression(data.expression, [variable_name])
        count = upper - lower + 1
        return sp.simplify(sp.summation(expression ** power, (variable, lower, upper)) / count)

    # ---- numeric (scipy) ----

    def compute_numeric(self, task: PythonMathTask, data: Any) -> tuple[Any, float | str]:

        if task == PythonMathTask.POISSON_PROBABILITY:
            numeric = float(stats.poisson.pmf(data.k, data.n))
            return numeric, numeric

        if task == PythonMathTask.NORMAL_CDF:
            raw_x = data.point or data.expression
            x = float(self.parse_bound(raw_x, "point", []))
            numeric = float(stats.norm.cdf(x))
            return numeric, numeric

        if task == PythonMathTask.NUMERIC_ROOT:
            variable = self.parse_variable(data.variable)
            variable_name = str(variable)
            expression = self.parse_expression(data.expression, [variable_name])
            func = sp.lambdify(variable, expression, modules=["numpy"])
            if data.lower is not None and data.upper is not None:
                root = optimize.root_scalar(
                    func,
                    bracket=[
                        float(self.parse_bound(data.lower, "lower", [])),
                        float(self.parse_bound(data.upper, "upper", [])),
                    ],
                )
                if not root.converged:
                    raise MathSolverError("numeric root search did not converge.")
                return float(root.root), float(root.root)

            start = float(self.parse_bound(data.point, "point", []))
            root = optimize.root(lambda values: [func(values[0])], [start])
            if not root.success:
                raise MathSolverError("numeric root search did not converge.")
            return float(root.x[0]), float(root.x[0])

        if task == PythonMathTask.NUMERIC_MINIMIZE:
            variable = self.parse_variable(data.variable)
            variable_name = str(variable)
            expression = self.parse_expression(data.expression, [variable_name])
            func = sp.lambdify(variable, expression, modules=["numpy"])
            lower = float(self.parse_bound(data.lower, "lower", []))
            upper = float(self.parse_bound(data.upper, "upper", []))
            result = optimize.minimize_scalar(func, bounds=(lower, upper), method="bounded")
            if not result.success:
                raise MathSolverError("numeric minimization did not converge.")
            exact = {"x": float(result.x), "fun": float(result.fun)}
            return exact, float(result.fun)

        raise ValueError(f"unsupported numeric task: {task}")
