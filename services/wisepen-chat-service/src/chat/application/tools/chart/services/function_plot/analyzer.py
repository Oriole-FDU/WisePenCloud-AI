from collections.abc import Callable

import numpy as np
import sympy as sp
from scipy import optimize

from chat.application.tools.chart.services.function_plot.models import (
    FunctionPlotFeature,
    LineSeries,
)

MAX_FEATURES = 5


class FunctionFeatureAnalyzer:
    """基于采样曲线检测零点和局部极值。

    该分析器只提供绘图辅助标注，不提供数学证明。失败候选会被跳过，
    不应阻断主图渲染。
    """

    def analyze(
        self,
        series: LineSeries,
        *,
        variable: str,
        detect_roots: bool,
        detect_extrema: bool,
    ) -> list[FunctionPlotFeature]:
        """分析单条一元曲线的特征点。

        Args:
            series: 一元函数采样曲线。
            variable: 自变量名。
            detect_roots: 是否检测零点。
            detect_extrema: 是否检测局部极值。

        Returns:
            最多 `MAX_FEATURES` 个特征点。
        """
        if not detect_roots and not detect_extrema:
            return []
        symbol = sp.Symbol(variable)
        fn = sp.lambdify(symbol, series.expression.sympy_expr, modules=["numpy"])
        features: list[FunctionPlotFeature] = []
        if detect_roots:
            features.extend(self._roots(series, fn))
        if detect_extrema:
            features.extend(self._extrema(series, fn))
        return features[:MAX_FEATURES]

    def _roots(self, series: LineSeries, fn: Callable[[float], float]) -> list[FunctionPlotFeature]:
        """用相邻采样点符号变化定位零点候选，再用 brentq refine。"""
        features: list[FunctionPlotFeature] = []
        x_values = series.x
        y_values = series.y
        for index in range(len(x_values) - 1):
            if len(features) >= MAX_FEATURES:
                break
            y0, y1 = y_values[index], y_values[index + 1]
            if not np.isfinite(y0) or not np.isfinite(y1):
                continue
            if y0 == 0:
                root = float(x_values[index])
            elif y0 * y1 > 0:
                continue
            else:
                try:
                    root = float(optimize.brentq(fn, float(x_values[index]), float(x_values[index + 1])))
                except Exception:
                    continue
            if self._too_close(root, features):
                continue
            features.append(
                FunctionPlotFeature(
                    kind="root",
                    expression=series.expression.raw,
                    x=root,
                    y=0.0,
                    method="brentq over sampled sign changes",
                )
            )
        return features

    def _extrema(self, series: LineSeries, fn: Callable[[float], float]) -> list[FunctionPlotFeature]:
        """用采样点找极值候选，再在局部区间用 minimize_scalar refine。"""
        candidates: list[tuple[str, int]] = []
        y_values = series.y
        for index in range(1, len(y_values) - 1):
            y_prev, y_mid, y_next = y_values[index - 1], y_values[index], y_values[index + 1]
            if not all(np.isfinite(value) for value in (y_prev, y_mid, y_next)):
                continue
            if y_mid <= y_prev and y_mid <= y_next:
                candidates.append(("local_min", index))
            elif y_mid >= y_prev and y_mid >= y_next:
                candidates.append(("local_max", index))

        if len(candidates) > MAX_FEATURES * 4:
            candidates = sorted(candidates, key=lambda item: abs(float(y_values[item[1]])), reverse=True)[: MAX_FEATURES * 4]

        features: list[FunctionPlotFeature] = []
        for kind, index in candidates:
            if len(features) >= MAX_FEATURES:
                break
            left = float(series.x[index - 1])
            right = float(series.x[index + 1])
            try:
                objective = fn if kind == "local_min" else lambda value: -float(fn(value))
                result = optimize.minimize_scalar(objective, bounds=(left, right), method="bounded")
                if not result.success:
                    continue
                x_value = float(result.x)
                y_value = float(fn(x_value))
            except Exception:
                continue
            if not np.isfinite(y_value) or self._too_close(x_value, features):
                continue
            features.append(
                FunctionPlotFeature(
                    kind=kind,
                    expression=series.expression.raw,
                    x=x_value,
                    y=y_value,
                    method="sampled candidate plus minimize_scalar refinement",
                )
            )
        return features

    def _too_close(self, x_value: float, features: list[FunctionPlotFeature]) -> bool:
        return any(feature.x is not None and abs(feature.x - x_value) < 1e-4 for feature in features)
