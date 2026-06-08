from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from chat.application.tools.chart.services.models import NoteTable


class NoteTableProvider(ABC):
    """Note 表格读取接口。

    当前仓库尚未接入真实 Note block 读取服务，因此先定义清晰接口。
    Demo 使用 MockNoteTableProvider 跑通 traceable_chart_from_note。
    """

    @abstractmethod
    async def get_table(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        block_id: str,
        resource_version: Optional[str],
    ) -> Optional[NoteTable]:
        """读取 Note block 中的表格。"""
        pass


class MockNoteTableProvider(NoteTableProvider):
    """Mock Note 表格读取器。

    - 用于 demo 和当前内测阶段。
    - 数据形状对齐 internal_note / parsed_note 的 block table。
    """

    def __init__(self) -> None:
        self._tables: Dict[str, NoteTable] = {
            "note_mock:block_metrics": NoteTable(
                resource_kind="internal_note",
                resource_id="note_mock",
                resource_version="v1",
                block_id="block_metrics",
                columns=["model", "accuracy", "latency"],
                rows=[
                    ["A", 0.91, 120],
                    ["B", 0.87, 96],
                    ["C", 0.93, 142],
                ],
            )
        }

    async def get_table(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        block_id: str,
        resource_version: Optional[str],
    ) -> Optional[NoteTable]:
        """返回 mock 表格。"""
        table = self._tables.get(f"{resource_id}:{block_id}")
        if table is None:
            return None
        return NoteTable(
            resource_kind=resource_kind,
            resource_id=resource_id,
            resource_version=resource_version or table.resource_version,
            block_id=block_id,
            columns=list(table.columns),
            rows=[list(row) for row in table.rows],
        )
