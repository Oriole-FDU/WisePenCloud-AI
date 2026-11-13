from wisepen_mcp.domain.entities.base import ApiModel


class DocumentSearchText(ApiModel):
    resource_id: str = ""
    version: int | None = None
    search_text: str = ""


__all__ = ["DocumentSearchText"]
