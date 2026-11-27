from __future__ import annotations

from common.mcp import ToolCategoryMeta, get_tool_metas, register_tool_categories


CATEGORY_TOOLS: dict[str, frozenset[str]] = {
    "sandbox_command": frozenset({
        "start_command", "get_command_result", "kill_command", "execute_command", "run_python",
    }),
    "sandbox_file": frozenset({
        "read_file", "view_image", "write_file", "replace_in_file", "search_in_file",
        "find_files", "grep_files", "glob_files", "list_files", "upload_file", "download_file",
        "upload_file_to_cloud_drive",
    }),
    "browser": frozenset({
        "browser_back", "browser_clear_cookies", "browser_click", "browser_close_tab",
        "browser_evaluate", "browser_execute_action", "browser_fill", "browser_forward",
        "browser_get_cookies", "browser_get_info", "browser_hotkey", "browser_hover",
        "browser_list_tabs", "browser_navigate", "browser_new_tab", "browser_press_key",
        "browser_reload", "browser_screenshot", "browser_scroll", "browser_set_cookies",
        "browser_snapshot", "browser_switch_tab",
    }),
}

TOOL_CATEGORIES: tuple[ToolCategoryMeta, ...] = (
    ToolCategoryMeta(
        name="sandbox_command",
        display_name="沙箱命令",
        description=(
            "Run shell commands or Python in the current session workspace and inspect or terminate "
            "asynchronous command tasks."
        ),
    ),
    ToolCategoryMeta(
        name="sandbox_file",
        display_name="沙箱文件",
        description=(
            "Read, inspect, search, create, edit, import, and export files in the current session workspace."
        ),
    ),
    ToolCategoryMeta(
        name="browser",
        display_name="浏览器",
        description=(
            "Navigate and interact with web pages in the sandbox browser, inspect page state, manage tabs "
            "and cookies, and capture screenshots."
        ),
    ),
)

_TOOL_CATEGORY: dict[str, str] = {}
for _category, _tool_names in CATEGORY_TOOLS.items():
    for _tool_name in _tool_names:
        if _tool_name in _TOOL_CATEGORY:
            raise RuntimeError(f"tool belongs to multiple categories: {_tool_name}")
        _TOOL_CATEGORY[_tool_name] = _category

def apply_sandbox_tool_categories() -> None:
    """把模型加载类别赋给已注册 meta"""
    metas = {meta.tool_name: meta for meta in get_tool_metas()}
    uncategorized = sorted(set(metas) - set(_TOOL_CATEGORY))
    if uncategorized:
        raise RuntimeError(f"sandbox tool metas have no category: {uncategorized}")
    for tool_name, meta in metas.items():
        meta.category = _TOOL_CATEGORY[tool_name]

    register_tool_categories(TOOL_CATEGORIES)


__all__ = [
    "CATEGORY_TOOLS",
    "TOOL_CATEGORIES",
    "apply_sandbox_tool_categories",
]
