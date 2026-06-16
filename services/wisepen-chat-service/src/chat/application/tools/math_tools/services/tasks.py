from __future__ import annotations

from enum import StrEnum


class CalculusTask(StrEnum):
    """微积分工具支持的结构化任务。"""

    DIFFERENTIATE = "differentiate"
    PARTIAL_DIFFERENTIATE = "partial_differentiate"
    INTEGRATE = "integrate"
    DEFINITE_INTEGRAL = "definite_integral"
    LIMIT = "limit"
    TAYLOR_SERIES = "taylor_series"
    SUMMATION = "summation"
    SOLVE_ODE = "solve_ode"
    DOUBLE_INTEGRAL = "double_integral"
    LAPLACE_TRANSFORM = "laplace_transform"

    @classmethod
    def values(cls) -> list[str]:
        return sorted(task.value for task in cls)


class LinearAlgebraTask(StrEnum):
    """线性代数工具支持的结构化任务。"""

    DETERMINANT = "determinant"
    TRACE = "trace"
    RANK = "rank"
    INVERSE = "inverse"
    RREF = "rref"
    EIGENVALUES = "eigenvalues"
    LINEAR_SOLVE = "linear_solve"
    MATRIX_MULTIPLY = "matrix_multiply"
    SVD = "svd"
    QR_DECOMPOSITION = "qr_decomposition"
    NULL_SPACE = "null_space"
    MATRIX_POWER = "matrix_power"

    @classmethod
    def values(cls) -> list[str]:
        return sorted(task.value for task in cls)


class EquationTask(StrEnum):
    """方程、不等式和优化工具支持的结构化任务。"""

    SOLVE_EQUATION = "solve_equation"
    SOLVE_SYSTEM = "solve_system"
    NUMERIC_ROOT = "numeric_root"
    SOLVE_INEQUALITY = "solve_inequality"
    NUMERIC_MINIMIZE = "numeric_minimize"
    CONSTRAINED_MINIMIZE = "constrained_minimize"

    @classmethod
    def values(cls) -> list[str]:
        return sorted(task.value for task in cls)


class StatsTask(StrEnum):
    """统计工具支持的结构化任务。"""

    BINOMIAL_PROB = "binomial_prob"
    POISSON_PROB = "poisson_prob"
    NORMAL_CDF = "normal_cdf"
    UNIFORM_EXPECTATION_VARIANCE = "uniform_expectation_variance"
    DESCRIPTIVE_STATS = "descriptive_stats"
    T_CDF = "t_cdf"
    CHI2_CDF = "chi2_cdf"
    F_CDF = "f_cdf"
    LINEAR_REGRESSION = "linear_regression"
    CORRELATION = "correlation"

    @classmethod
    def values(cls) -> list[str]:
        return sorted(task.value for task in cls)


class ExpressionTask(StrEnum):
    """表达式、组合和轻量数论工具支持的结构化任务。"""

    SIMPLIFY = "simplify"
    EXPAND = "expand"
    FACTOR = "factor"
    NUMERIC = "numeric"
    FACTORIAL = "factorial"
    COMBINATIONS = "combinations"
    PERMUTATIONS = "permutations"
    GCD = "gcd"
    LCM = "lcm"
    PRIME_FACTORS = "prime_factors"

    @classmethod
    def values(cls) -> list[str]:
        return sorted(task.value for task in cls)