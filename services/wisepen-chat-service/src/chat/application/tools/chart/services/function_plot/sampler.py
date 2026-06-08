import numpy as np
import sympy as sp

from chat.application.tools.chart.services.function_plot.errors import FunctionPlotError
from chat.application.tools.chart.services.function_plot.models import (
    GridSample,
    LineSeries,
    ParsedExpression,
)


class FunctionSampler:
    """函数数值采样器。

    输入是已解析的 SymPy 表达式和可信 request 字段，输出 renderer 可直接使用
    的 NumPy 数组。这里不重新校验 schema 字段，只处理数值计算中的异常和不可绘制值。
    """

    def sample_line(
        self,
        expression: ParsedExpression,
        *,
        variable: str,
        x_range: tuple[float, float],
        samples: int,
    ) -> LineSeries:
        """采样一元函数曲线。

        Args:
            expression: 已解析表达式。
            variable: 自变量名。
            x_range: x 轴采样范围。
            samples: 采样点数。

        Returns:
            曲线采样结果。

        Raises:
            FunctionPlotError: 表达式无法 lambdify 或采样结果形状不可用。
        """
        symbol = sp.Symbol(variable)
        x_values = np.linspace(x_range[0], x_range[1], samples)
        try:
            fn = sp.lambdify(symbol, expression.sympy_expr, modules=["numpy"])
            raw_y = fn(x_values)
            y_values = np.asarray(raw_y, dtype=float)
        except Exception as exc:
            raise FunctionPlotError(f"failed to sample expression '{expression.raw}': {exc}") from exc

        if y_values.shape == ():
            y_values = np.full_like(x_values, float(y_values), dtype=float)
        if y_values.shape != x_values.shape:
            try:
                y_values = np.broadcast_to(y_values, x_values.shape).astype(float)
            except ValueError as exc:
                raise FunctionPlotError(f"sampled expression has invalid shape: {y_values.shape}") from exc

        warnings = self._clean_line_values(y_values)
        return LineSeries(expression=expression, x=x_values, y=y_values, warnings=warnings)

    def sample_grid(
        self,
        expression: ParsedExpression,
        *,
        variables: list[str],
        x_range: tuple[float, float],
        y_range: tuple[float, float],
        samples: int,
    ) -> GridSample:
        """采样二元函数网格。

        Args:
            expression: 已解析表达式。
            variables: 二元变量，按 `[x, y]` 顺序传入。
            x_range: x 轴采样范围。
            y_range: y 轴采样范围。
            samples: 每个轴的采样点数。

        Returns:
            曲面/等高线共用的网格采样结果。

        Raises:
            FunctionPlotError: 表达式无法采样或结果形状不可用。
        """
        x_symbol, y_symbol = sp.Symbol(variables[0]), sp.Symbol(variables[1])
        x_values = np.linspace(x_range[0], x_range[1], samples)
        y_values = np.linspace(y_range[0], y_range[1], samples)
        x_grid, y_grid = np.meshgrid(x_values, y_values)
        try:
            fn = sp.lambdify((x_symbol, y_symbol), expression.sympy_expr, modules=["numpy"])
            raw_z = fn(x_grid, y_grid)
            z_values = np.asarray(raw_z, dtype=float)
        except Exception as exc:
            raise FunctionPlotError(f"failed to sample expression '{expression.raw}': {exc}") from exc

        if z_values.shape == ():
            z_values = np.full_like(x_grid, float(z_values), dtype=float)
        if z_values.shape != x_grid.shape:
            try:
                z_values = np.broadcast_to(z_values, x_grid.shape).astype(float)
            except ValueError as exc:
                raise FunctionPlotError(f"sampled grid has invalid shape: {z_values.shape}") from exc

        warnings = []
        invalid_count = int((~np.isfinite(z_values)).sum())
        if invalid_count:
            warnings.append(f"{invalid_count} non-finite grid samples were hidden.")
            z_values[~np.isfinite(z_values)] = np.nan
        return GridSample(expression=expression, x=x_grid, y=y_grid, z=z_values, warnings=warnings)

    def _clean_line_values(self, y_values: np.ndarray) -> list[str]:
        """清理一元函数采样值。

        非 finite 值和大跳变不能直接连线，否则渐近线附近会出现误导性竖线。
        """
        warnings = []
        invalid_count = int((~np.isfinite(y_values)).sum())
        if invalid_count:
            warnings.append(f"{invalid_count} non-finite samples were hidden.")
            y_values[~np.isfinite(y_values)] = np.nan

        finite_y = y_values[np.isfinite(y_values)]
        if finite_y.size < 3:
            return warnings

        diffs = np.abs(np.diff(y_values))
        finite_diffs = diffs[np.isfinite(diffs)]
        if finite_diffs.size == 0:
            return warnings

        median_diff = float(np.nanmedian(finite_diffs))
        threshold = max(50.0, median_diff * 50.0)
        jump_mask = np.isfinite(diffs) & (diffs > threshold)
        jump_count = int(jump_mask.sum())
        if jump_count:
            y_values[1:][jump_mask] = np.nan
            warnings.append(f"{jump_count} large jumps were split to avoid connecting discontinuities.")
        return warnings
