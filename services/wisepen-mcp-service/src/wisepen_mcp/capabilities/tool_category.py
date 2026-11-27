from __future__ import annotations

from common.mcp import ToolCategoryMeta, get_tool_metas, register_tool_categories


CATEGORY_TOOLS: dict[str, frozenset[str]] = {
    "skill_resource": frozenset({
        "create_skill_info", "get_skill_info", "update_skill_info", "upload_skill_draft_asset",
    }),
    "agent_resource": frozenset({
        "create_agent_info", "get_agent_info", "get_agent_version_bundle_info",
        "update_agent_info", "update_agent_spec",
    }),
    "note_resource": frozenset({
        "apply_current_note_edits", "create_note", "read_current_note_for_edit", "read_note_resource_text",
    }),
    "resource_file": frozenset({
        "create_folder", "get_resource_base_info", "get_resource_tag_tree",
        "get_resource_user_interaction_record", "list_resources", "mount_resources_to_group",
        "move_folders", "move_resources_in_group", "move_resources_to_folder",
        "move_resources_to_trash", "remove_folders", "rename_resource", "search_resources",
        "unmount_resources_from_group", "update_folder",
    }),
    "resource_comment": frozenset({
        "add_inline_comment_item", "change_inline_comment_resolve_status", "create_comment",
        "create_comment_reply", "delete_comment_item", "delete_inline_comment_item",
        "list_comment_replies", "list_comments", "list_inline_comments", "update_inline_comment_item",
    }),
    "resource_favorite": frozenset({
        "change_resource_favorite_status", "create_favorite_collection", "delete_favorite_collection",
        "get_resource_favorite_status", "list_favorite_collections", "list_favorited_resources",
        "update_favorite_collection_info",
    }),
    "resource_permission": frozenset({
        "change_group_resource_config", "change_resource_action_permission", "get_group_resource_config",
    }),
    "document_resource": frozenset({"get_document_source_file", "read_document_resource_text"}),
    "group": frozenset({
        "create_group", "get_group_base_info", "join_group", "list_group_members",
        "list_group_resource_daily_metrics", "list_groups",
    }),
    "user": frozenset({
        "get_my_user_info", "get_my_wallet_info", "get_unread_message_count", "list_my_messages",
        "list_my_task_status", "list_my_wallet_transactions",
    }),
    "web_search": frozenset({
        "anysearch_search", "baidu_qianfan_search", "default_web_search", "exa_search",
        "firecrawl_search", "tavily_search", "tinyfish_search",
    }),
}

TOOL_CATEGORIES: tuple[ToolCategoryMeta, ...] = (
    ToolCategoryMeta(
        name="skill_resource",
        display_name="技能资源",
        description="Create, inspect, update, and upload assets for WisePen Skill drafts.",
    ),
    ToolCategoryMeta(
        name="agent_resource",
        display_name="Agent 资源",
        description="Create, inspect, and update WisePen AIAsset Agent records and draft specifications.",
    ),
    ToolCategoryMeta(
        name="note_resource",
        display_name="笔记",
        description=(
            "Create, read, and apply structured edits to WisePen notes. "
            "This category does not organize resource folders or manage comments."
        ),
    ),
    ToolCategoryMeta(
        name="resource_file",
        display_name="资源文件",
        description=(
            "Search, inspect, organize, rename, move, delete, mount, and unmount WisePen resources "
            "and folders, and inspect resource interaction metadata. This category does not manage "
            "comments, favorites, note contents, or permissions."
        ),
    ),
    ToolCategoryMeta(
        name="resource_comment",
        display_name="资源评论与批注",
        description="Read and manage resource comments, replies, and inline annotations attached to content.",
    ),
    ToolCategoryMeta(
        name="resource_favorite",
        display_name="资源收藏",
        description="Favorite or unfavorite resources and create, inspect, update, or delete favorite collections.",
    ),
    ToolCategoryMeta(
        name="resource_permission",
        display_name="资源权限",
        description="Inspect and change group resource configuration and resource action permissions.",
    ),
    ToolCategoryMeta(
        name="document_resource",
        display_name="文档资源",
        description="Read ordinary document resource text or retrieve its original source file.",
    ),
    ToolCategoryMeta(
        name="group",
        display_name="小组",
        description="Create, join, inspect, and list WisePen groups, members, and group resource metrics.",
    ),
    ToolCategoryMeta(
        name="user",
        display_name="用户账户",
        description="Inspect the current user's profile, wallet, transactions, messages, and task status.",
    ),
    ToolCategoryMeta(
        name="web_search",
        display_name="网页搜索",
        description="Search external web or academic sources using search providers configured for the user.",
    ),
)

_TOOL_CATEGORY: dict[str, str] = {}
for _category, _tool_names in CATEGORY_TOOLS.items():
    for _tool_name in _tool_names:
        if _tool_name in _TOOL_CATEGORY:
            raise RuntimeError(f"tool belongs to multiple categories: {_tool_name}")
        _TOOL_CATEGORY[_tool_name] = _category


def tool_category(tool_name: str) -> str | None:
    return _TOOL_CATEGORY.get(tool_name)


def apply_tool_categories() -> None:
    """把模型加载类别赋给已注册 meta"""
    metas = {meta.tool_name: meta for meta in get_tool_metas()}
    missing_metas = sorted(set(_TOOL_CATEGORY) - set(metas))
    uncategorized = sorted(set(metas) - set(_TOOL_CATEGORY))
    if missing_metas:
        raise RuntimeError(f"tool category references unknown metas: {missing_metas}")
    if uncategorized:
        raise RuntimeError(f"tool metas have no category: {uncategorized}")

    for tool_name, category in _TOOL_CATEGORY.items():
        metas[tool_name].category = category

    register_tool_categories(TOOL_CATEGORIES)


__all__ = ["CATEGORY_TOOLS", "TOOL_CATEGORIES", "apply_tool_categories", "tool_category"]
