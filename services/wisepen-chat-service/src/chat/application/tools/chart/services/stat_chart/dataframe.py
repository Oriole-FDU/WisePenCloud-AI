from typing import Any

import pandas as pd

from chat.application.tools.chart.services.stat_chart.errors import StatChartError
from chat.application.tools.chart.services.stat_chart.models import StatChartDataInput

MAX_ROWS = 5000
MAX_COLUMNS = 50


class DataFrameBuilder:
    """从 tool inline records 构造 pandas DataFrame。

    这是数据输入边界，负责资源上限和列结构检查。service 只调用结果，
    不重复处理 LLM 原始 records。
    """

    def build(self, data: StatChartDataInput) -> tuple[pd.DataFrame, list[str]]:
        """构造 DataFrame 并做轻量类型推断。

        Args:
            data: tool 层收敛后的 inline records 输入。

        Returns:
            DataFrame 和非阻断 warning 列表。

        Raises:
            StatChartError: records 为空、超限或无法构造表格。
        """
        if len(data.records) > MAX_ROWS:
            raise StatChartError(f"records must contain at most {MAX_ROWS} rows.")

        try:
            frame = pd.DataFrame.from_records(data.records)
        except Exception as exc:
            raise StatChartError(f"failed to build DataFrame from records: {exc}") from exc

        if frame.empty:
            raise StatChartError("records must produce a non-empty table.")

        if data.columns is not None:
            missing = [column for column in data.columns if column not in frame.columns]
            if missing:
                raise StatChartError(f"columns not found in records: {', '.join(missing)}")
            frame = frame.loc[:, data.columns]

        if len(frame.columns) > MAX_COLUMNS:
            raise StatChartError(f"data must contain at most {MAX_COLUMNS} columns.")

        warnings: list[str] = []
        frame = frame.copy()
        frame.columns = [str(column) for column in frame.columns]
        for column in frame.columns:
            converted = pd.to_numeric(frame[column], errors="coerce")
            # 只在绝大多数非空值都可数值化时转换，避免把分类编码误伤成大量 NaN。
            if self._numeric_ratio(frame[column], converted) >= 0.9:
                frame[column] = converted
        return frame, warnings

    def _numeric_ratio(self, original: pd.Series, converted: pd.Series) -> float:
        """计算非空值中可转换为数值的比例。"""
        non_empty = original.map(_has_value)
        total = int(non_empty.sum())
        if total == 0:
            return 0.0
        return float(converted[non_empty].notna().sum() / total)


def _has_value(value: Any) -> bool:
    """空字符串保留为字符串，但类型推断时不算有效数值候选。"""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True
