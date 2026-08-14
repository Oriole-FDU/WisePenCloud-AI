from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.note_ai.apply_current_note_edits import register_apply_current_note_edits_tool
from wisepen_mcp.capabilities.note_ai.read_current_note_for_edit import register_read_current_note_for_edit_tool
from wisepen_mcp.capabilities.note_ai.read_note_resource_text import register_read_note_resource_text_tool
from wisepen_mcp.service_client import NoteClient, NoteCollabClient, ResourceClient


def register_note_ai_tools(
    mcp: FastMCP,
    note_collab_client: NoteCollabClient,
    resource_client: ResourceClient,
    note_client: NoteClient,
) -> None:
    register_read_current_note_for_edit_tool(mcp, note_collab_client)
    register_apply_current_note_edits_tool(mcp, note_collab_client)
    register_read_note_resource_text_tool(mcp, resource_client, note_client)

__all__ = ["register_note_ai_tools"]
