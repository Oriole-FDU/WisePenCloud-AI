import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from playwright.async_api import ElementHandle, Frame, Page

from chat.application.tools.browser.services.browser_interact.models import (
    SnapshotPayload,
    ToolError,
)
from chat.application.tools.browser.services.browser_interact.response.error_factory import (
    make_snapshot_required_error,
    make_stale_ref_error,
)
from common.logger import log_fail

REF_PATTERN = re.compile(r"e[1-9][0-9]*")
SNAPSHOT_ID_BYTES = 8

INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "treeitem",
    "Iframe",
    "iframe",
}
CONTENT_ROLES = {
    "heading",
    "cell",
    "gridcell",
    "columnheader",
    "rowheader",
    "listitem",
    "article",
    "region",
    "main",
    "navigation",
}
STRUCTURAL_SKIP_ROLES = {"RootWebArea", "WebArea"}
INVISIBLE_CHARS_RE = re.compile("[\ufeff\u200b\u200c\u200d\u2060\u00a0]")

GOAL_SYNONYMS = {
    "find": "search",
    "query": "search",
    "lookup": "search",
    "look": "search",
    "搜索": "search",
    "查找": "search",
    "searchbox": "search",
    "field": "input",
    "box": "input",
    "textbox": "input",
    "输入": "input",
    "邮箱": "email",
    "邮件": "email",
    "mail": "email",
    "username": "user",
    "login": "signin",
    "log": "signin",
    "signin": "signin",
    "登录": "signin",
    "password": "password",
    "密码": "password",
    "submit": "submit",
    "button": "button",
}
FIELD_WEIGHTS = {
    "label": 10,
    "name": 10,
    "placeholder": 10,
    "ariaLabel": 10,
    "value": 8,
    "role": 6,
    "checked": 4,
    "tag": 2,
    "className": 1,
}


@dataclass(slots=True)
class CursorElementInfo:
    kind: str
    hints: List[str] = field(default_factory=list)
    text: str = ""
    hidden_input_kind: Optional[str] = None
    hidden_input_checked: Optional[str] = None


@dataclass(slots=True)
class SnapshotNode:
    role: str = ""
    name: str = ""
    value: str = ""
    backend_node_id: Optional[int] = None
    child_ids: List[str] = field(default_factory=list)
    children: List[int] = field(default_factory=list)
    parent_idx: Optional[int] = None
    depth: int = 0
    level: Optional[int] = None
    checked: Optional[str] = None
    expanded: Optional[bool] = None
    selected: Optional[bool] = None
    disabled: Optional[bool] = None
    required: Optional[bool] = None
    ref: Optional[str] = None
    nth: Optional[int] = None
    cursor_info: Optional[CursorElementInfo] = None
    url: Optional[str] = None
    ignored: bool = False


@dataclass(frozen=True, slots=True)
class RefEntry:
    ref: str
    role: str
    name: str
    nth: Optional[int] = None
    backend_node_id: Optional[int] = None
    frame_path: Tuple[int, ...] = ()
    frame_id: Optional[str] = None
    bounds: Optional[Dict[str, Any]] = None
    in_viewport: bool = False


class RefMap:
    def __init__(self) -> None:
        self._entries: Dict[str, RefEntry] = {}

    def clear(self) -> None:
        self._entries.clear()

    def add(self, entry: RefEntry) -> None:
        self._entries[entry.ref] = entry

    def get(self, ref: str) -> Optional[RefEntry]:
        return self._entries.get(ref)

    def entries_sorted(self) -> List[Tuple[str, RefEntry]]:
        return sorted(self._entries.items(), key=lambda item: ref_sort_key(item[0]))

    def filtered(self, refs: Set[str]) -> "RefMap":
        next_map = RefMap()
        for ref in refs:
            entry = self._entries.get(ref)
            if entry is not None:
                next_map.add(entry)
        return next_map


class SnapshotError(RuntimeError):
    pass


def ref_sort_key(ref: str) -> int:
    try:
        return int(ref.removeprefix("e"))
    except ValueError:
        return 10**12


def parse_ref(value: str) -> Optional[str]:
    if not isinstance(value, str):
        return None

    trimmed = value.strip()
    for prefix in ("@", "ref="):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix) :].strip()
            break

    if REF_PATTERN.fullmatch(trimmed):
        return trimmed
    return None


def ref_entry_metadata(entry: RefEntry) -> Dict[str, Any]:
    """将内部 RefEntry 转成对响应安全的 ref 诊断元数据。"""
    metadata = {
        "role": entry.role,
        "name": entry.name,
        "frame_path": list(entry.frame_path),
        "frame_id": entry.frame_id,
        "in_viewport": entry.in_viewport,
    }
    if entry.bounds is not None:
        metadata["bounds"] = entry.bounds
    return metadata


async def resolve_ref_element(page: Page, ref_map: RefMap, ref: str) -> Optional[ElementHandle]:
    parsed = parse_ref(ref)
    if parsed is None:
        raise ValueError("ref must match e1/e2/e123 format")

    entry = ref_map.get(parsed)
    if entry is None:
        return None

    if entry.backend_node_id is not None:
        element = await element_from_backend_node_id(
            page,
            entry.backend_node_id,
            entry.frame_path,
        )
        if element is not None:
            return element

    if entry.role and entry.name:
        backend_node_id = await find_backend_node_id_by_role_name(
            page,
            entry.role,
            entry.name,
            entry.nth,
            entry.frame_path,
            entry.frame_id,
        )
        if backend_node_id is not None:
            return await element_from_backend_node_id(
                page,
                backend_node_id,
                entry.frame_path,
            )

    return None


async def element_from_backend_node_id(
    page: Page,
    backend_node_id: int,
    frame_path: Tuple[int, ...] = (),
) -> Optional[ElementHandle]:
    frame = frame_from_path(page, frame_path)
    if frame is None:
        return None

    cdp = await new_cdp_session_with_page_fallback(page, frame)
    try:
        resolved = await cdp.send(
            "DOM.resolveNode",
            {
                "backendNodeId": backend_node_id,
                "objectGroup": "wisepen-browser-interact",
            },
        )
        object_id = (
            resolved.get("object", {}).get("objectId")
            if isinstance(resolved, dict)
            else None
        )
        if not object_id:
            return None

        # Playwright cannot adopt raw CDP object IDs into JSHandle. Use a DOM-side
        # marker as the bridge, then immediately remove it after query_selector.
        marker = f"wisepen-{secrets.token_hex(8)}"
        await cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": (
                    "function(marker) {"
                    "this.setAttribute('data-wisepen-ref-resolve', marker);"
                    "return true;"
                    "}"
                ),
                "arguments": [{"value": marker}],
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
        selector = f'[data-wisepen-ref-resolve="{marker}"]'
        element = await frame.query_selector(selector)
        if element is not None:
            await element.evaluate("el => el.removeAttribute('data-wisepen-ref-resolve')")
        else:
            await remove_ref_resolve_markers(frame, marker)
        return element
    except Exception:
        return None
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


def frame_from_path(page: Page, frame_path: Tuple[int, ...]) -> Optional[Frame]:
    frame = page.main_frame
    for child_index in frame_path:
        children = frame.child_frames
        if child_index < 0 or child_index >= len(children):
            return None
        frame = children[child_index]
    return frame


def frame_path_for_child(frame_path: Tuple[int, ...], child_index: int) -> Tuple[int, ...]:
    return (*frame_path, child_index)


async def frame_id_for_frame(page: Page, frame: Frame) -> Optional[str]:
    """读取 Playwright Frame 对应的 CDP frameId。"""
    cdp = None
    try:
        cdp = await page.context.new_cdp_session(frame)
        result = await cdp.send("Page.getFrameTree")
    except Exception:
        return None
    finally:
        if cdp is not None:
            try:
                await cdp.detach()
            except Exception:
                pass

    frame_tree = result.get("frameTree", {}) if isinstance(result, dict) else {}
    frame_info = frame_tree.get("frame", {}) if isinstance(frame_tree, dict) else {}
    frame_id = frame_info.get("id") if isinstance(frame_info, dict) else None
    return frame_id if isinstance(frame_id, str) and frame_id else None


async def remove_ref_resolve_markers(frame: Frame, marker: str) -> None:
    script = """
        marker => document.querySelectorAll('[data-wisepen-ref-resolve]')
          .forEach(el => {
            if (el.getAttribute('data-wisepen-ref-resolve') === marker) {
              el.removeAttribute('data-wisepen-ref-resolve');
            }
          })
    """
    try:
        await frame.evaluate(script, marker)
    except Exception:
        pass


async def element_bounds_from_backend_node_id(
    page: Page,
    backend_node_id: Optional[int],
    frame_path: Tuple[int, ...],
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """读取 ref 元素的视口坐标，用于视觉定位和点击诊断。"""
    if backend_node_id is None:
        return None, False

    element = await element_from_backend_node_id(page, backend_node_id, frame_path)
    if element is None:
        return None, False

    try:
        data = await element.evaluate(
            """el => {
                const rect = el.getBoundingClientRect();
                const width = window.innerWidth || document.documentElement.clientWidth || 0;
                const height = window.innerHeight || document.documentElement.clientHeight || 0;
                const visible =
                  rect.width > 0 &&
                  rect.height > 0 &&
                  rect.bottom >= 0 &&
                  rect.right >= 0 &&
                  rect.top <= height &&
                  rect.left <= width;
                return {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    center_x: Math.round(rect.x + rect.width / 2),
                    center_y: Math.round(rect.y + rect.height / 2),
                    in_viewport: visible
                };
            }"""
        )
    except Exception:
        return None, False
    finally:
        await element.dispose()

    if not isinstance(data, dict):
        return None, False

    in_viewport = bool(data.pop("in_viewport", False))
    return data, in_viewport


async def find_backend_node_id_by_role_name(
    page: Page,
    role: str,
    name: str,
    nth: Optional[int],
    frame_path: Tuple[int, ...] = (),
    frame_id: Optional[str] = None,
) -> Optional[int]:
    nodes = await get_full_ax_tree(page, frame_path, frame_id)
    match_index = nth or 0
    seen = 0

    for node in nodes:
        if node.get("ignored") is True:
            continue
        if ax_value_to_string(node.get("role")) != role:
            continue
        if ax_value_to_string(node.get("name")) != name:
            continue
        if seen == match_index:
            backend_node_id = node.get("backendDOMNodeId")
            return backend_node_id if isinstance(backend_node_id, int) else None
        seen += 1

    return None


async def get_full_ax_tree(
    page: Page,
    frame_path: Tuple[int, ...] = (),
    frame_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    frame = frame_from_path(page, frame_path)
    if frame is None:
        return []

    cdp = None
    params: Dict[str, Any] = {}
    try:
        cdp = await page.context.new_cdp_session(frame)
    except Exception:
        if not frame_id:
            return []
        cdp = await page.context.new_cdp_session(page)
        params = {"frameId": frame_id}

    try:
        await cdp.send("DOM.enable")
        await cdp.send("Accessibility.enable")
        result = await cdp.send("Accessibility.getFullAXTree", params)
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass

    nodes = result.get("nodes", []) if isinstance(result, dict) else []
    return nodes if isinstance(nodes, list) else []


async def ax_snapshot_elements(page: Page) -> Tuple[List[Dict[str, Any]], RefMap]:
    ref_map = RefMap()
    elements, _ = await collect_ax_snapshot_elements(
        page,
        frame_path=(),
        base_depth=0,
        next_ref=1,
        ref_map=ref_map,
        frame_depth=0,
    )
    return elements, ref_map


async def collect_ax_snapshot_elements(
    page: Page,
    *,
    frame_path: Tuple[int, ...],
    frame_id: Optional[str] = None,
    base_depth: int,
    next_ref: int,
    ref_map: RefMap,
    frame_depth: int,
) -> Tuple[List[Dict[str, Any]], int]:
    nodes = await get_full_ax_tree(page, frame_path, frame_id)
    if not nodes:
        raise SnapshotError("Accessibility.getFullAXTree returned no nodes")

    tree_nodes, root_indices = build_ax_tree(nodes)
    cursor_elements = await find_cursor_interactive_elements(page, frame_path)
    promote_hidden_inputs(tree_nodes, cursor_elements)
    next_ref = assign_refs(tree_nodes, cursor_elements, start_ref=next_ref)

    for node in tree_nodes:
        if not node.ref:
            continue
        bounds, in_viewport = await element_bounds_from_backend_node_id(
            page,
            node.backend_node_id,
            frame_path,
        )
        ref_map.add(
            RefEntry(
                ref=node.ref,
                role=node.role,
                name=node.name,
                nth=node.nth,
                backend_node_id=node.backend_node_id,
                frame_path=frame_path,
                frame_id=frame_id,
                bounds=bounds,
                in_viewport=in_viewport,
            )
        )

    elements: List[Dict[str, Any]] = []
    for root_idx in root_indices:
        render_ax_elements(tree_nodes, root_idx, base_depth, elements, frame_path)

    if frame_depth >= 1:
        return elements, next_ref

    frame = frame_from_path(page, frame_path)
    if frame is None or not frame.child_frames:
        return elements, next_ref

    used_child_indices: Set[int] = set()
    expanded_elements: List[Dict[str, Any]] = []
    for element in elements:
        expanded_elements.append(element)
        if element.get("role") != "iframe":
            continue

        iframe_backend_node_id = element.get("backendNodeId")
        if not isinstance(iframe_backend_node_id, int):
            continue

        child_frame_id = await resolve_iframe_frame_id(
            page,
            iframe_backend_node_id,
            frame_path,
        )
        child_index = None
        if child_frame_id:
            for idx, child_frame in enumerate(frame.child_frames):
                if idx in used_child_indices:
                    continue
                if await frame_id_for_frame(page, child_frame) == child_frame_id:
                    child_index = idx
                    break
        if child_index is None:
            for idx, _ in enumerate(frame.child_frames):
                if idx not in used_child_indices:
                    child_index = idx
                    break
        if child_index is None:
            continue

        used_child_indices.add(child_index)
        child_path = frame_path_for_child(frame_path, child_index)
        try:
            child_elements, next_ref = await collect_ax_snapshot_elements(
                page,
                frame_path=child_path,
                frame_id=child_frame_id,
                base_depth=int(element.get("depth") or 0) + 1,
                next_ref=next_ref,
                ref_map=ref_map,
                frame_depth=frame_depth + 1,
            )
        except Exception:
            continue
        expanded_elements.extend(child_elements)

    return expanded_elements, next_ref


async def new_cdp_session_with_page_fallback(page: Page, frame: Frame):
    try:
        return await page.context.new_cdp_session(frame)
    except Exception:
        return await page.context.new_cdp_session(page)


async def resolve_iframe_frame_id(
    page: Page,
    backend_node_id: int,
    frame_path: Tuple[int, ...],
) -> Optional[str]:
    if frame_from_path(page, frame_path) is None:
        return None

    cdp = await page.context.new_cdp_session(page)
    try:
        await cdp.send("DOM.enable")
        result = await cdp.send(
            "DOM.describeNode",
            {"backendNodeId": backend_node_id, "depth": 1},
        )
    except Exception:
        return None
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass

    node = result.get("node", {}) if isinstance(result, dict) else {}
    content_document = node.get("contentDocument") if isinstance(node, dict) else None
    content_frame_id = (
        content_document.get("frameId")
        if isinstance(content_document, dict)
        else None
    )
    node_frame_id = node.get("frameId") if isinstance(node, dict) else None
    frame_id = content_frame_id or node_frame_id
    return frame_id if isinstance(frame_id, str) and frame_id else None


def build_ax_tree(nodes: Sequence[Dict[str, Any]]) -> Tuple[List[SnapshotNode], List[int]]:
    tree_nodes: List[SnapshotNode] = []
    id_to_idx: Dict[str, int] = {}

    for idx, raw in enumerate(nodes):
        node_id = str(raw.get("nodeId", idx))
        role = ax_value_to_string(raw.get("role"))
        ignored = bool(raw.get("ignored"))
        if (ignored and role != "RootWebArea") or role == "InlineTextBox":
            tree_node = SnapshotNode(ignored=True)
        else:
            properties = extract_properties(raw.get("properties"))
            tree_node = SnapshotNode(
                role=role,
                name=clean_display_text(ax_value_to_string(raw.get("name"))),
                value=clean_display_text(ax_value_to_string(raw.get("value"))),
                backend_node_id=raw.get("backendDOMNodeId")
                if isinstance(raw.get("backendDOMNodeId"), int)
                else None,
                child_ids=[
                    str(child_id)
                    for child_id in raw.get("childIds", [])
                    if child_id is not None
                ],
                ignored=ignored,
                **properties,
            )
        tree_nodes.append(tree_node)
        id_to_idx[node_id] = idx

    for idx, node in enumerate(tree_nodes):
        for child_id in node.child_ids:
            child_idx = id_to_idx.get(child_id)
            if child_idx is None:
                continue
            node.children.append(child_idx)
            tree_nodes[child_idx].parent_idx = idx

    aggregate_static_text(tree_nodes)

    root_indices = [
        idx for idx, node in enumerate(tree_nodes) if node.parent_idx is None and node.role
    ]
    for root_idx in root_indices:
        set_depth(tree_nodes, root_idx, 0)

    return tree_nodes, root_indices


def extract_properties(properties: Any) -> Dict[str, Any]:
    values = {
        "level": None,
        "checked": None,
        "expanded": None,
        "selected": None,
        "disabled": None,
        "required": None,
    }
    if not isinstance(properties, list):
        return values

    for prop in properties:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        raw_value = prop.get("value")
        value = raw_value.get("value") if isinstance(raw_value, dict) else None
        if name == "level" and isinstance(value, int):
            values["level"] = value
        elif name == "checked" and value is not None:
            values["checked"] = str(value).lower()
        elif name == "expanded" and isinstance(value, bool):
            values["expanded"] = value
        elif name == "selected" and isinstance(value, bool):
            values["selected"] = value
        elif name == "disabled" and isinstance(value, bool):
            values["disabled"] = value
        elif name == "required" and isinstance(value, bool):
            values["required"] = value

    return values


def aggregate_static_text(tree_nodes: List[SnapshotNode]) -> None:
    for node in tree_nodes:
        if not node.role or not node.children:
            continue

        start = 0
        children = list(node.children)
        while start < len(children):
            child = tree_nodes[children[start]]
            if child.role != "StaticText":
                start += 1
                continue

            end = start + 1
            while end < len(children) and tree_nodes[children[end]].role == "StaticText":
                end += 1

            if end > start + 1:
                tree_nodes[children[start]].name = clean_display_text(
                    "".join(tree_nodes[children[i]].name for i in range(start, end))
                )
                for idx in range(start + 1, end):
                    clear_node(tree_nodes[children[idx]])

            start = end

        if len(children) == 1:
            child = tree_nodes[children[0]]
            if child.role == "StaticText" and child.name == node.name:
                clear_node(child)


def clear_node(node: SnapshotNode) -> None:
    node.role = ""
    node.name = ""
    node.value = ""
    node.backend_node_id = None
    node.children = []
    node.child_ids = []
    node.ref = None
    node.cursor_info = None


def set_depth(tree_nodes: List[SnapshotNode], idx: int, depth: int) -> None:
    tree_nodes[idx].depth = depth
    for child_idx in list(tree_nodes[idx].children):
        set_depth(tree_nodes, child_idx, depth + 1)


async def find_cursor_interactive_elements(
    page: Page,
    frame_path: Tuple[int, ...] = (),
) -> Dict[int, CursorElementInfo]:
    frame = frame_from_path(page, frame_path)
    if frame is None:
        return {}

    script = r"""() => {
        const results = [];
        if (!document.body) return results;

        const interactiveRoles = new Set([
          'button', 'link', 'textbox', 'checkbox', 'radio', 'combobox', 'listbox',
          'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'searchbox',
          'slider', 'spinbutton', 'switch', 'tab', 'treeitem'
        ]);
        const interactiveTags = new Set(['a', 'button', 'input', 'select', 'textarea', 'details', 'summary']);
        const elements = Array.from(document.body.querySelectorAll('*'));

        for (const el of elements) {
          if (el.closest && el.closest('[hidden], [aria-hidden="true"]')) continue;

          const tagName = el.tagName.toLowerCase();
          if (interactiveTags.has(tagName)) continue;

          const role = (el.getAttribute('role') || '').toLowerCase();
          if (interactiveRoles.has(role)) continue;

          const style = getComputedStyle(el);
          const hasCursorPointer = style.cursor === 'pointer';
          const hasOnClick = el.hasAttribute('onclick') || el.onclick !== null;
          const tabIndex = el.getAttribute('tabindex');
          const hasTabIndex = tabIndex !== null && tabIndex !== '-1';
          const contentEditable = el.getAttribute('contenteditable');
          const isEditable = contentEditable === '' || contentEditable === 'true';
          if (!hasCursorPointer && !hasOnClick && !hasTabIndex && !isEditable) continue;

          if (hasCursorPointer && !hasOnClick && !hasTabIndex && !isEditable) {
            const parent = el.parentElement;
            if (parent && getComputedStyle(parent).cursor === 'pointer') continue;
          }

          const rect = el.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0) continue;

          let hiddenInputType = null;
          let hiddenInputChecked = null;
          const hiddenInput = el.querySelector('input[type="radio"], input[type="checkbox"]');
          if (hiddenInput) {
            const inputStyle = getComputedStyle(hiddenInput);
            const isHidden = inputStyle.display === 'none' || inputStyle.visibility === 'hidden' || hiddenInput.hidden;
            if (isHidden) {
              hiddenInputType = hiddenInput.type;
              hiddenInputChecked = hiddenInput.indeterminate ? 'mixed' : String(hiddenInput.checked);
            }
          }

          const marker = String(results.length);
          el.setAttribute('data-wisepen-ci', marker);
          results.push({
            marker,
            text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100),
            hasCursorPointer,
            hasOnClick,
            hasTabIndex,
            isEditable,
            hiddenInputType,
            hiddenInputChecked
          });
        }
        return results;
    }"""
    raw_elements = await frame.evaluate(script)
    if not isinstance(raw_elements, list) or not raw_elements:
        return {}

    try:
        cdp = await page.context.new_cdp_session(frame)
    except Exception as exc:
        log_fail("CDP session", str(exc))
        return {}

    marker_to_backend: Dict[str, int] = {}
    try:
        await cdp.send("DOM.enable")
        document = await cdp.send("DOM.getDocument", {"depth": 0})
        root_node_id = document.get("root", {}).get("nodeId")
        if not isinstance(root_node_id, int):
            return {}

        query_result = await cdp.send(
            "DOM.querySelectorAll",
            {"nodeId": root_node_id, "selector": "[data-wisepen-ci]"},
        )
        node_ids = query_result.get("nodeIds", [])
        if not isinstance(node_ids, list):
            node_ids = []

        for node_id in node_ids:
            if not isinstance(node_id, int):
                continue
            desc = await cdp.send("DOM.describeNode", {"nodeId": node_id})
            raw_node = desc.get("node", {})
            backend_node_id = raw_node.get("backendNodeId")
            marker = flat_attrs_get(raw_node.get("attributes"), "data-wisepen-ci")
            if isinstance(backend_node_id, int) and marker is not None:
                marker_to_backend[marker] = backend_node_id
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass
        try:
            await frame.evaluate(
                "() => document.querySelectorAll('[data-wisepen-ci]').forEach(el => el.removeAttribute('data-wisepen-ci'))"
            )
        except Exception:
            pass

    result: Dict[int, CursorElementInfo] = {}
    for item in raw_elements:
        if not isinstance(item, dict):
            continue
        marker = str(item.get("marker", ""))
        backend_node_id = marker_to_backend.get(marker)
        if backend_node_id is None:
            continue

        has_cursor_pointer = bool(item.get("hasCursorPointer"))
        has_on_click = bool(item.get("hasOnClick"))
        has_tab_index = bool(item.get("hasTabIndex"))
        is_editable = bool(item.get("isEditable"))
        if has_cursor_pointer or has_on_click:
            kind = "clickable"
        elif is_editable:
            kind = "editable"
        else:
            kind = "focusable"

        hints = []
        if has_cursor_pointer:
            hints.append("cursor:pointer")
        if has_on_click:
            hints.append("onclick")
        if has_tab_index:
            hints.append("tabindex")
        if is_editable:
            hints.append("contenteditable")

        hidden_input_kind = item.get("hiddenInputType")
        if hidden_input_kind not in ("radio", "checkbox"):
            hidden_input_kind = None

        result[backend_node_id] = CursorElementInfo(
            kind=kind,
            hints=hints,
            text=clean_display_text(str(item.get("text") or "")),
            hidden_input_kind=hidden_input_kind,
            hidden_input_checked=str(item.get("hiddenInputChecked"))
            if item.get("hiddenInputChecked") is not None
            else None,
        )

    return result


def promote_hidden_inputs(
    tree_nodes: List[SnapshotNode],
    cursor_elements: Dict[int, CursorElementInfo],
) -> None:
    for node in tree_nodes:
        if node.role not in {"LabelText", "generic"}:
            continue
        if node.backend_node_id is None:
            continue
        cursor_info = cursor_elements.get(node.backend_node_id)
        if cursor_info is None or cursor_info.hidden_input_kind is None:
            continue
        node.role = cursor_info.hidden_input_kind
        if not node.name and cursor_info.text:
            node.name = cursor_info.text
        if cursor_info.hidden_input_checked is not None:
            node.checked = cursor_info.hidden_input_checked


def assign_refs(
    tree_nodes: List[SnapshotNode],
    cursor_elements: Dict[int, CursorElementInfo],
    *,
    start_ref: int = 1,
) -> int:
    role_name_counts: Dict[Tuple[str, str], int] = {}
    candidates: List[Tuple[int, int]] = []

    for idx, node in enumerate(tree_nodes):
        should_ref = False
        if node.role in INTERACTIVE_ROLES:
            should_ref = True
        elif node.role in CONTENT_ROLES and node.name:
            should_ref = True
        elif node.backend_node_id is not None and node.backend_node_id in cursor_elements:
            should_ref = True

        if not should_ref:
            continue

        key = (node.role, node.name)
        nth = role_name_counts.get(key, 0)
        role_name_counts[key] = nth + 1
        candidates.append((idx, nth))

    duplicate_keys = {key for key, count in role_name_counts.items() if count > 1}

    for ref_num, (idx, nth) in enumerate(candidates, start=start_ref):
        node = tree_nodes[idx]
        node.ref = f"e{ref_num}"
        if (node.role, node.name) in duplicate_keys:
            node.nth = nth
        if node.backend_node_id is not None:
            node.cursor_info = cursor_elements.get(node.backend_node_id)

    return start_ref + len(candidates)


def render_ax_elements(
    tree_nodes: List[SnapshotNode],
    idx: int,
    indent: int,
    output: List[Dict[str, Any]],
    frame_path: Tuple[int, ...] = (),
) -> None:
    node = tree_nodes[idx]

    if (
        not node.role
        or (node.role == "generic" and not node.ref and len(node.children) <= 1)
        or (node.role == "StaticText" and not clean_display_text(node.name))
    ):
        for child_idx in node.children:
            render_ax_elements(tree_nodes, child_idx, indent, output, frame_path)
        return

    if node.role in STRUCTURAL_SKIP_ROLES:
        for child_idx in node.children:
            render_ax_elements(tree_nodes, child_idx, indent, output, frame_path)
        return

    if not node.ref:
        for child_idx in node.children:
            render_ax_elements(tree_nodes, child_idx, indent, output, frame_path)
        return

    label = node.name or (node.cursor_info.text if node.cursor_info else "")
    flags = element_flags(node)
    output.append(
        {
            "ref": node.ref,
            "role": role_for_output(node.role),
            "label": label,
            "name": node.name,
            "value": node.value,
            "flags": flags,
            "level": node.level,
            "checked": node.checked,
            "expanded": node.expanded,
            "selected": node.selected,
            "disabled": node.disabled,
            "required": node.required,
            "url": node.url,
            "depth": indent,
            "backendNodeId": node.backend_node_id,
            "fillable": node.role in {"textbox", "searchbox"},
            "clickable": node.role in INTERACTIVE_ROLES
            or (node.cursor_info is not None and node.cursor_info.kind == "clickable"),
            "inViewport": True,
            "source": "ax",
            "framePath": frame_path,
            "cursorKind": node.cursor_info.kind if node.cursor_info else None,
            "cursorHints": list(node.cursor_info.hints) if node.cursor_info else [],
        }
    )

    for child_idx in node.children:
        render_ax_elements(tree_nodes, child_idx, indent + 1, output, frame_path)


def role_for_output(role: str) -> str:
    return "iframe" if role == "Iframe" else role


def element_flags(node: SnapshotNode) -> List[str]:
    flags = []
    if node.role in {"textbox", "searchbox"}:
        flags.append("fillable")
    if node.role in {"Iframe", "iframe"}:
        flags.append("frame")
    if node.cursor_info is not None:
        flags.append(node.cursor_info.kind)
        flags.extend(node.cursor_info.hints)
    return list(dict.fromkeys(flags))


def format_field_value(value: str) -> str:
    return (
        clean_display_text(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .strip()
    )


def format_snapshot_tree(elements: List[Dict[str, Any]]) -> str:
    lines = []
    for element in elements:
        ref = element.get("ref", "")
        role = element.get("role") or element.get("tag") or "element"
        label = str(element.get("label") or element.get("name") or "").strip()
        flags = [str(flag) for flag in element.get("flags") or [] if flag]
        depth = element.get("depth")
        indent = "  " * depth if isinstance(depth, int) and depth > 0 else ""

        attrs = [f"ref={ref}"]
        for attr in ("level", "checked", "expanded"):
            value = element.get(attr)
            if value is not None:
                attrs.append(f"{attr}={value}")
        if element.get("selected"):
            attrs.append("selected")
        if element.get("disabled"):
            attrs.append("disabled")
        if element.get("required"):
            attrs.append("required")
        if element.get("url"):
            attrs.append(f"url={element['url']}")
        frame_path = element.get("framePath")
        if isinstance(frame_path, tuple) and frame_path:
            attrs.append("frame=" + ".".join(str(part) for part in frame_path))

        if flags:
            attrs.append("flags=" + ",".join(flags))

        line = f"{indent}- {role}"
        if label:
            line += f' "{format_field_value(label)}"'
        line += f" [{', '.join(attrs)}]"

        value = str(element.get("value") or "")
        if value and value != label:
            line += f": {format_field_value(value)}"

        lines.append(line)

    return "\n".join(lines)


def focused_elements(
    elements: List[Dict[str, Any]],
    goal: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    terms = normalize_goal_terms(goal)

    def score(element: Dict[str, Any]) -> int:
        value = 0
        phrase = normalize_text(goal or "")

        for field_name, weight in FIELD_WEIGHTS.items():
            text = normalize_text(str(element.get(field_name) or ""))
            if not text:
                continue

            if phrase and phrase in text:
                value += weight * 3

            for term in terms:
                if term in text:
                    value += weight

        role = normalize_text(str(element.get("role") or ""))
        element_type = normalize_text(str(element.get("type") or ""))

        if "search" in terms and role in ("searchbox", "search"):
            value += 12
        if "search" in terms and element_type == "search":
            value += 10
        if "email" in terms and element_type == "email":
            value += 14
        if "password" in terms and element_type == "password":
            value += 14
        if "input" in terms and element.get("fillable"):
            value += 8
        if "submit" in terms and role == "button":
            value += 8

        if element.get("inViewport"):
            value += 4
        if element.get("fillable"):
            value += 10
        if element.get("clickable"):
            value += 3

        if not terms and element.get("inViewport"):
            value += 2

        return value

    indexed = list(enumerate(elements))
    indexed.sort(key=lambda item: (-score(item[1]), item[0]))
    return [element for _, element in indexed[:limit]]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def normalize_goal_terms(goal: Optional[str]) -> List[str]:
    normalized_goal = normalize_text(goal or "")
    raw_terms = [term for term in re.split(r"\W+", normalized_goal) if term]
    terms = []

    for term in raw_terms:
        normalized = GOAL_SYNONYMS.get(term, term)
        terms.append(normalized)
        if normalized != term:
            terms.append(term)

    for alias, canonical in GOAL_SYNONYMS.items():
        if alias in normalized_goal:
            terms.append(canonical)
            terms.append(alias)

    return list(dict.fromkeys(terms))


def ax_value_to_string(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    raw = value.get("value")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bool):
        return str(raw).lower()
    if isinstance(raw, (int, float)):
        return str(raw)
    return ""


def clean_display_text(value: str) -> str:
    return re.sub(r"\s+", " ", INVISIBLE_CHARS_RE.sub("", value or "")).strip()


def flat_attrs_get(attrs: Any, name: str) -> Optional[str]:
    if not isinstance(attrs, list):
        return None
    for idx, value in enumerate(attrs):
        if value == name and idx + 1 < len(attrs):
            next_value = attrs[idx + 1]
            return str(next_value) if next_value is not None else ""
    return None


class SnapshotManager:
    def __init__(self) -> None:
        self._current_snapshot_id: Optional[str] = None
        self._ref_map = RefMap()

    @property
    def current_snapshot_id(self) -> Optional[str]:
        return self._current_snapshot_id

    @property
    def ref_map(self) -> RefMap:
        return self._ref_map

    def invalidate(self) -> None:
        self._current_snapshot_id = None
        self._ref_map.clear()

    def resolve_current(
        self, snapshot_id: Optional[str]
    ) -> Tuple[Optional[str], Optional[ToolError]]:
        if snapshot_id is None:
            if self._current_snapshot_id is None:
                return None, make_snapshot_required_error()
            return self._current_snapshot_id, None

        if snapshot_id != self._current_snapshot_id:
            return None, make_stale_ref_error(snapshot_id)

        return self._current_snapshot_id, None

    async def resolve_element(self, page: Page, ref: str) -> Optional[ElementHandle]:
        return await resolve_ref_element(page, self._ref_map, ref)

    def ref_metadata(self, ref: str) -> Optional[Dict[str, Any]]:
        """返回当前 snapshot 中某个 ref 的 role/name/frame 元数据。"""
        parsed = parse_ref(ref)
        if parsed is None:
            return None

        entry = self._ref_map.get(parsed)
        if entry is None:
            return None

        return ref_entry_metadata(entry)

    def current_refs_metadata(self) -> Dict[str, Dict[str, Any]]:
        """返回当前 snapshot 所有 ref 元数据，用于截图叠加层。"""
        return {
            ref: ref_entry_metadata(entry)
            for ref, entry in self._ref_map.entries_sorted()
        }

    async def take(
        self,
        page: Page,
        *,
        mode: str = "full",
        goal: Optional[str] = None,
        limit: Optional[int] = None,
        scope_ref: Optional[str] = None,
    ) -> SnapshotPayload:
        elements, ref_map = await ax_snapshot_elements(page)

        if scope_ref:
            parsed_scope_ref = parse_ref(scope_ref)
            if parsed_scope_ref is None:
                raise ValueError(
                    f"scope ref must be a valid snapshot ref, got {scope_ref}"
                )
            scope_element = next(
                (
                    element
                    for element in elements
                    if element.get("ref") == parsed_scope_ref
                ),
                None,
            )
            if scope_element is None:
                raise ValueError(f"scope ref not found in current page: {scope_ref}")

            scope_depth = int(scope_element.get("depth") or 0)
            scope_index = elements.index(scope_element)
            scoped_elements = [{**scope_element, "depth": 0}]
            for element in elements[scope_index + 1 :]:
                depth = int(element.get("depth") or 0)
                if depth <= scope_depth:
                    break
                scoped_elements.append({**element, "depth": max(0, depth - scope_depth)})
            elements = scoped_elements
            ref_map = ref_map.filtered(
                {str(element.get("ref")) for element in elements if element.get("ref")}
            )

        if mode == "full":
            pass
        elif mode == "focused":
            if limit is None:
                limit = 30
            elif limit < 1 or limit > 200:
                raise ValueError(
                    f"limit must be between 1 and 200, got {limit}"
                )
            elements = [
                {**element, "depth": 0}
                for element in focused_elements(elements, goal, limit)
            ]
            ref_map = ref_map.filtered(
                {str(element.get("ref")) for element in elements if element.get("ref")}
            )
        else:
            raise ValueError(f"unsupported snapshot mode: {mode}")

        tree = format_snapshot_tree(elements) or "(empty page)"

        snapshot_id = secrets.token_hex(SNAPSHOT_ID_BYTES)
        self._current_snapshot_id = snapshot_id
        self._ref_map = ref_map

        return SnapshotPayload(
            snapshot_id=snapshot_id,
            tree=tree,
            mode=mode,
            goal=goal,
            scope_ref=parse_ref(scope_ref) if scope_ref else None,
            refs={
                ref: ref_entry_metadata(entry)
                for ref, entry in self._ref_map.entries_sorted()
            },
        )
