"""公司内部合同 metadata 的只读校验与向量业务增量。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from rag.core.schema import Chunk


class EmploymentMetadata(BaseModel):
    """上游员工与入职日期；部门关系另从正文抽取，不改写原始 metadata。"""

    # Chunk 还会保存平台生成的图事实；只解析业务字段，不删除或覆盖其他字段。
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    employee: str = Field(min_length=1, description="雇员名称。")
    start_date: date = Field(
        description="入职日期，JSON 使用 ISO YYYY-MM-DD；year 由此派生。"
    )


def employment_projection(chunk: Chunk) -> dict[str, object]:
    """只返回业务增量；资源/revision 定位由通用 VectorIndexer 保证。"""
    data = EmploymentMetadata.model_validate(chunk.metadata)
    return {"year": data.start_date.year, "employee": data.employee}


class EmploymentMetadataTransformer:
    """只读校验原始 Document metadata，保留 Chunker 已深拷贝的业务值。"""

    async def transform(self, chunks, *, document=None):
        # 空文档也必须校验合同，不因零 Chunk 绕过业务输入约束。
        EmploymentMetadata.model_validate(document.metadata)
        return chunks
