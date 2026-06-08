from enum import StrEnum


class PythonMathTask(StrEnum):
    SIMPLIFY = "simplify"
    EXPAND = "expand"
    FACTOR = "factor"
    SOLVE_EQUATION = "solve_equation"
    SOLVE_SYSTEM = "solve_system"
    DIFFERENTIATE = "differentiate"
    INTEGRATE = "integrate"
    DEFINITE_INTEGRAL = "definite_integral"
    LIMIT = "limit"
    TAYLOR_SERIES = "taylor_series"
    NUMERIC = "numeric"
    MATRIX_DETERMINANT = "matrix_determinant"
    MATRIX_TRACE = "matrix_trace"
    MATRIX_RANK = "matrix_rank"
    MATRIX_INVERSE = "matrix_inverse"
    MATRIX_RREF = "matrix_rref"
    MATRIX_EIGENVALUES = "matrix_eigenvalues"
    MATRIX_SOLVE = "matrix_solve"
    MATRIX_MULTIPLY = "matrix_multiply"
    FACTORIAL = "factorial"
    BINOMIAL = "binomial"
    PERMUTATION = "permutation"
    SUMMATION = "summation"
    BINOMIAL_PROBABILITY = "binomial_probability"
    POISSON_PROBABILITY = "poisson_probability"
    NORMAL_CDF = "normal_cdf"
    EXPECTATION = "expectation"
    VARIANCE = "variance"
    NUMERIC_ROOT = "numeric_root"
    NUMERIC_MINIMIZE = "numeric_minimize"


PYTHON_MATH_TASKS = frozenset(task.value for task in PythonMathTask)