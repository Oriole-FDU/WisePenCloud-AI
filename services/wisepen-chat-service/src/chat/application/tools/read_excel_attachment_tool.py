from io import BytesIO
from typing import Dict, Any

from common.logger import log_error, log_event
from chat.application.tools.read_attachment_tool import BaseReadAttachmentTool


class ReadExcelAttachmentTool(BaseReadAttachmentTool):
    """读取 Excel 附件（.xlsx / .xls）"""

    @property
    def name(self) -> str:
        return "read_excel_attachment"

    @property
    def description(self) -> str:
        return (
            "Read the content of a Microsoft Excel attachment file (.xlsx or .xls). "
            "Extracts data from all sheets in TSV format with sheet headers. "
            "Call with the object_key of the Excel attachment you want to read."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "object_key": {
                    "type": "string",
                    "description": "The OSS object key of the Excel attachment to read.",
                },
            },
            "required": ["object_key"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        object_key = self._resolve_object_key(kwargs)
        if object_key is None:
            return "[Tool Error] Missing required argument: object_key."

        content, error = await self._validate_and_download(context, object_key)
        if error:
            return error

        try:
            from openpyxl import load_workbook

            wb = load_workbook(BytesIO(content), data_only=True)
            text = _extract_xlsx_text(wb)
        except Exception:
            # 旧格式 .xls 回退 → xlrd
            try:
                import xlrd

                wb = xlrd.open_workbook(file_contents=content)
                text = _extract_xls_text(wb)
            except Exception:
                log_error("Excel解析失败", None, object_key=object_key)
                return "[Tool Error] Failed to parse Excel (not a valid .xlsx or .xls file)."

        text = self._truncate(text)
        log_event("Excel附件读取成功", object_key=object_key, content_length=len(text))
        return text


def _extract_xlsx_text(wb) -> str:
    parts: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        parts.append(f"--- Sheet: {sheet_name} ---")
        row_count = 0
        for row in ws.iter_rows(values_only=True):
            row_text = "\t".join(
                str(cell) if cell is not None else "" for cell in row
            )
            if row_text.strip():
                parts.append(row_text)
                row_count += 1
        if row_count == 0:
            parts.append("(empty sheet)")
    return "\n".join(parts)


def _extract_xls_text(wb) -> str:
    parts: list[str] = []
    for sheet_name in wb.sheet_names():
        ws = wb.sheet_by_name(sheet_name)
        parts.append(f"--- Sheet: {sheet_name} ---")
        row_count = 0
        for row_idx in range(ws.nrows):
            row_text = "\t".join(
                str(ws.cell_value(row_idx, col_idx)) for col_idx in range(ws.ncols)
            )
            if row_text.strip():
                parts.append(row_text)
                row_count += 1
        if row_count == 0:
            parts.append("(empty sheet)")
    return "\n".join(parts)
