from typing import Any, Dict, List, Optional

from chat.application.tools.chart.services.models import (
    ChartRenderPayload,
    ChartTableRequest,
    FunctionPlotRequest,
)
from chat.application.tools.chart.services.note_provider import NoteTableProvider
from chat.application.tools.chart.services.output_adapter import ChartTempOutputAdapter
from chat.application.tools.chart.services.renderer import (
    QuickChartRenderer,
    TraceableMatplotlibRenderer,
)


class QuickChartService:
    """普通 session 绘图服务。

    - 不理解自然语言。
    - 不读取业务数据库。
    - 不做入参校验，只渲染 tool 层传入的规范化请求。
    """

    def __init__(
        self,
        *,
        output_adapter: ChartTempOutputAdapter,
        renderer: QuickChartRenderer,
    ) -> None:
        """初始化普通绘图服务。"""
        self._output_adapter = output_adapter
        self._renderer = renderer

    async def render_table_chart(
        self,
        *,
        user_id: str,
        session_id: str,
        request: ChartTableRequest,
    ) -> ChartRenderPayload:
        """渲染普通表格图。"""
        generated = await self._output_adapter.write_chart(
            user_id=user_id,
            session_id=session_id,
            title=request.title,
            output_format=request.output_format,
            render=lambda path: self._renderer.render_table_chart(request, path),
        )
        return ChartRenderPayload(
            chart_type=request.chart_type,
            title=request.title,
            output_format=request.output_format,
            generated=generated,
            source_mode="session_input",
            traceable=False,
        )

    async def render_function_plot(
        self,
        *,
        user_id: str,
        session_id: str,
        request: FunctionPlotRequest,
    ) -> ChartRenderPayload:
        """渲染函数图。"""
        generated = await self._output_adapter.write_chart(
            user_id=user_id,
            session_id=session_id,
            title=request.title,
            output_format=request.output_format,
            render=lambda path: self._renderer.render_function_plot(request, path),
        )
        return ChartRenderPayload(
            chart_type="function",
            title=request.title,
            output_format=request.output_format,
            generated=generated,
            source_mode="session_input",
            traceable=False,
        )


class TraceableChartService:
    """可追溯 Note 图表服务。

    - 只从 NoteTableProvider 读取 block/table 数据。
    - 可追溯渲染后端固定为 Matplotlib。
    - 当前 source_map 追到 Note block，能定位 cell 时补 row_index/column_name。
    """

    def __init__(
        self,
        *,
        output_adapter: ChartTempOutputAdapter,
        renderer: TraceableMatplotlibRenderer,
        note_table_provider: NoteTableProvider,
    ) -> None:
        """初始化可追溯图表服务。"""
        self._output_adapter = output_adapter
        self._renderer = renderer
        self._note_table_provider = note_table_provider

    async def render_from_note(
        self,
        *,
        user_id: str,
        session_id: str,
        resource_kind: str,
        resource_id: str,
        block_id: str,
        resource_version: Optional[str],
        request: ChartTableRequest,
    ) -> ChartRenderPayload:
        """从 Note block 表格渲染可追溯图。"""
        table = await self._note_table_provider.get_table(
            resource_kind=resource_kind,
            resource_id=resource_id,
            block_id=block_id,
            resource_version=resource_version,
        )
        if table is None:
            raise ValueError("NOTE_TABLE_NOT_FOUND")

        note_request = ChartTableRequest(
            chart_type=request.chart_type,
            title=request.title,
            caption=request.caption,
            columns=table.columns,
            rows=table.rows,
            x_column=request.x_column,
            y_columns=request.y_columns,
            label_column=request.label_column,
            value_column=request.value_column,
            color_column=request.color_column,
            bin_count=request.bin_count,
            annotations=request.annotations,
            x_log_scale=request.x_log_scale,
            y_log_scale=request.y_log_scale,
            y_range=request.y_range,
            output_format="svg",
        )

        source_map_holder: Dict[str, Dict[str, Any]] = {}

        def render(path) -> None:
            source_map_holder.update(self._renderer.render(note_request, path))

        generated = await self._output_adapter.write_chart(
            user_id=user_id,
            session_id=session_id,
            title=note_request.title,
            output_format="svg",
            render=render,
        )

        base = {
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            "resource_version": resource_version,
            "block_id": block_id,
        }
        if not source_map_holder:
            source_map_holder["chart_block"] = dict(base)
        else:
            for element_id, target in list(source_map_holder.items()):
                source_map_holder[element_id] = {**base, **target}

        return ChartRenderPayload(
            chart_type=note_request.chart_type,
            title=note_request.title,
            output_format="svg",
            generated=generated,
            source_mode="note_block",
            traceable=True,
            source_map=source_map_holder,
        )
