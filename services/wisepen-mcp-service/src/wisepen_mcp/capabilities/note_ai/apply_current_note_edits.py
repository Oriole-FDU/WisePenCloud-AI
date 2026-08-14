from __future__ import annotations

from typing import Annotated, Any, Literal

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import NoteCollabClient


PatchInlineMark = Literal["bold", "italic", "underline", "strike", "code"]
PatchBlockAttrPrimitive = str | int | float | bool


class PatchTextInlineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = Field(description="Plain inline text item.")
    text: str = Field(description="Text content.")
    marks: list[PatchInlineMark] | None = Field(default=None, description="Optional inline marks.")
    textColor: str | None = Field(default=None, description="Optional text color.")
    backgroundColor: str | None = Field(default=None, description="Optional background color.")


class PatchLinkInlineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["link"] = Field(description="Inline link item.")
    text: str = Field(description="Visible link text.")
    href: Annotated[str, Field(min_length=1, description="Link target URL.")]
    marks: list[PatchInlineMark] | None = Field(default=None, description="Optional inline marks.")


class PatchInlineMathItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["inlineMath"] = Field(description="Inline math item.")
    expression: str = Field(description="Math expression.")


PatchInlineItem = Annotated[
    PatchTextInlineItem | PatchLinkInlineItem | PatchInlineMathItem,
    Field(discriminator="type"),
]


class PatchInlineContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["inline"] = Field(description="Inline content for paragraph-like blocks.")
    items: list[PatchInlineItem] = Field(description="Ordered inline content items.")


class PatchTableContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["table"] = Field(description="Table content.")
    headerRows: int = Field(ge=0, description="Number of header rows.")
    headerCols: int = Field(ge=0, description="Number of header columns.")
    rows: list[list[list[PatchInlineItem]]] = Field(
        description="Rows, then cells, then inline items inside each cell."
    )


PatchContent = Annotated[
    PatchInlineContent | PatchTableContent,
    Field(discriminator="kind"),
]


class InsertBlockCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Annotated[str, Field(min_length=1, description="Block type, such as paragraph or heading.")]
    attrs: dict[str, PatchBlockAttrPrimitive] | None = Field(default=None, description="Optional block attrs.")
    content: PatchContent = Field(description="New block content.")


class ReplaceContentOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opId: Annotated[str, Field(min_length=1, description="Unique id for this operation within the patch.")]
    kind: Literal["replaceContent"] = Field(description="Replace content of an existing public block id.")
    blockId: Annotated[str, Field(min_length=1, description="Public block id returned by read_current_note_for_edit.")]
    content: PatchContent = Field(description="Replacement content.")


class DeleteBlockOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opId: Annotated[str, Field(min_length=1, description="Unique id for this operation within the patch.")]
    kind: Literal["deleteBlock"] = Field(description="Delete an existing public block id.")
    blockId: Annotated[str, Field(min_length=1, description="Public block id returned by read_current_note_for_edit.")]


class InsertBlockOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opId: Annotated[str, Field(min_length=1, description="Unique id for this operation within the patch.")]
    kind: Literal["insertBlock"] = Field(description="Insert a block relative to an existing public block id.")
    anchorBlockId: Annotated[str, Field(min_length=1, description="Public anchor block id returned by read_current_note_for_edit.")]
    position: Literal["before", "after"] = Field(description="Insert before or after anchorBlockId.")
    block: InsertBlockCandidate = Field(description="Block to insert.")


PatchOperation = Annotated[
    ReplaceContentOperation | DeleteBlockOperation | InsertBlockOperation,
    Field(discriminator="kind"),
]


def register_apply_current_note_edits_tool(mcp: FastMCP, note_collab_client: NoteCollabClient) -> None:
    @mcp.tool(
        name="apply_current_note_edits",
        description=(
            "Apply structured edit suggestions to the currently open Wisepen note. Use the yjs-v1 version and "
            "public block ids returned by read_current_note_for_edit. Operations must use exact fields: opId, kind, "
            "blockId or anchorBlockId, and structured content."
        ),
    )
    async def apply_current_note_edits(
        resource_id: Annotated[str, Field(description="Current open note resource id used for the latest read.")],
        patch_id: Annotated[str, Field(description="Stable idempotency id for this exact edit attempt.")],
        version: Annotated[str, Field(pattern=r"^yjs-v1:", description="yjs-v1 version from read_current_note_for_edit.")],
        operations: Annotated[
            list[PatchOperation],
            Field(min_length=1, max_length=200, description="Structured note edit operations."),
        ],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)
        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.NOTE_AI_REQUEST_INVALID, "resource_id must not be blank.")
        return await note_collab_client.apply_note_ai_diff(
            resource_id,
            {
                "patchId": patch_id,
                "version": version,
                "operations": [
                    operation.model_dump(mode="json", exclude_none=True)
                    for operation in operations
                ],
            },
        )


__all__ = ["register_apply_current_note_edits_tool"]
