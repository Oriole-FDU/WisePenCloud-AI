from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeGuard

from common.logger import warn
from pydantic import TypeAdapter

from chat.application.tools.core.execution.result import ToolOutput
from chat.application.tools.core.output_cache.cache_store import put_tool_content

_TRUNCATION_MARKER = "\n...[truncated]...\n"
_JSON_ADAPTER = TypeAdapter(Any)

_Container = dict[str, Any] | list[Any]  # JSON 树中可被原地改写的父节点
_Slot = str | int  # 目标在父节点中的位置：dict 的键 / list 的下标


def cacheable_tool_output(
    func: Callable[..., Any] | None = None,
    *,
    paths: tuple[str, ...] = (),
) -> Callable[..., Any]:
    """把工具返回值中的长字符串原地替换为 preview 和缓存回执。

    被装饰函数必须是 async，且由框架以关键字参数注入
    “context={"session_id": ...}”。

    用法::

        @cacheable_tool_output                              # 只处理根字符串 / 根列表第一层
        @cacheable_tool_output(paths=("results.*.text",))   # 嵌套对象必须显式声明路径

    路径语法（paths）：

    - 以 “.” 分隔层级；“*” 匹配“字典的任意键 / 列表的任意元素”。
    - 路径终点必须落在字符串上；列表上的非 “*” token 会隐式穿透到每个元素，
      因此 “results.text” 与 “results.*.text” 等价。
    - 路径中的键不存在时静默跳过。
    - 不声明 paths：只处理根字符串，或根列表第一层的字符串元素。
    """

    def decorate(target: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(target):
            raise TypeError("cacheable_tool_output requires an async function")

        @wraps(target)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 先校验再执行工具，避免工具白跑一趟
            context = kwargs.get("context")
            if not isinstance(context, dict) or "session_id" not in context:
                raise TypeError(
                    "cacheable_tool_output requires kwargs['context'] "
                    "to be a dict containing 'session_id'"
                )
            raw = await target(*args, **kwargs)
            return await _claim_check_result(
                raw, paths=paths, session_id=context["session_id"]
            )

        return wrapper

    return decorate if func is None else decorate(func)


async def _claim_check_result(
    raw: Any,
    *,
    paths: tuple[str, ...],
    session_id: str,
) -> Any:
    """对工具返回值做 Claim Check，并保持返回类型与原返回值一致。"""
    if not isinstance(raw, ToolOutput):
        return await process_cacheable_output(raw, paths=paths, session_id=session_id)

    result = await process_cacheable_output(
        raw.content, paths=paths, session_id=session_id
    )
    # result 仍是 str 说明没有改写；否则是结构化数据，序列化回 content
    content = (
        result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    )
    return ToolOutput(content=content, images=raw.images)


async def process_cacheable_output(
    value: Any,
    *,
    paths: tuple[str, ...],
    session_id: str,
) -> Any:
    """在 Host 侧按路径声明执行 Claim Check，返回改写后的 JSON 纯树。

    流程：

    1. 归一化  任意返回值 → JSON 纯树（dict / list / str / 数字 ...）
    2. 收集    按 paths 找出可能需要寄存的字符串，记为 _Target
    3. 分预算  在“单条 / 总量”两级字符预算内，为每个 target 分配 preview 长度
    4. 寄存    写入 Store；仅当 preview 放不下全文（发生截断）时才改写输出

    输出形状：未截断时输出保持原样，不暴露任何缓存字段；截断时字符串被替换为
    三个同级字段（以键名 “text” 为例）::

        {"text": "<很长>"}
        → {"text_preview": "头...[truncated]...尾",
           "text_content_id": "<id>",
           "text_total_length": 12345}

    键名为 “content”、或目标是列表元素 / 根字符串时，字段名不带前缀
    （即 “preview” / “content_id” / “total_length”）。
    """
    from chat.core.config.app_settings import settings

    tree = _dump_json_tree(value)
    # 根字符串没有父节点可供原地替换，包进单元素列表，结束时再取出
    root = [tree] if isinstance(tree, str) else tree

    targets = _collect_targets(root, paths)
    if targets:
        budgets = _preview_budgets(
            [target.text for target in targets],
            per_budget=settings.TOOL_CONTENT_PREVIEW_PER_CHAR_BUDGET,
            total_budget=settings.TOOL_CONTENT_PREVIEW_TOTAL_CHAR_BUDGET,
        )
        # 逐个寄存；预算与 target 一一对应
        for target, budget in zip(targets, budgets, strict=True):
            await _claim_one(target, budget, session_id=session_id)

    return root[0] if isinstance(tree, str) else root


def _dump_json_tree(value: Any) -> Any:
    """把任意复杂对象（含 MCP 信封、本地工具返回值）展开为 JSON 纯数据。"""
    if isinstance(value, str):
        # 字符串可能本身就是 JSON（如工具直接返回 json.dumps 的结果），尝试还原
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return _JSON_ADAPTER.dump_python(value, mode="json")


@dataclass(slots=True)
class _Target:
    """一个待寄存的字符串，以及原地改写它所需的定位信息。"""

    parent: _Container
    slot: _Slot
    text: str

    def replace_with(self, replacement: dict[str, Any]) -> None:
        if isinstance(self.parent, dict):
            del self.parent[self.slot]
            self.parent.update(replacement)  # 回执字段作为同级键追加
        else:
            self.parent[self.slot] = replacement


def _is_claimable_text(value: Any) -> TypeGuard[str]:
    """非空白字符串才有寄存价值。"""
    return isinstance(value, str) and bool(value.strip())


def _collect_targets(root: _Container, paths: tuple[str, ...]) -> list[_Target]:
    """按 paths 在 JSON 树中收集待寄存的字符串；无 paths 时只看根列表第一层。

    root 必须是容器，根字符串由调用方先包进单元素列表。
    """
    if not paths:
        if not isinstance(root, list):
            return []
        # 无路径声明：仅处理根列表第一层的字符串
        return [
            _Target(root, index, item)
            for index, item in enumerate(root)
            if _is_claimable_text(item)
        ]

    # 以 (父节点 id, 槽位) 去重：多条路径命中同一字符串只记一次，且保持发现顺序
    found: dict[tuple[int, _Slot], _Target] = {}
    for path in paths:
        tokens = tuple(token for token in path.strip(".").split(".") if token)
        _collect_path(root, tokens, found)
    return list(found.values())


def _collect_path(
    node: Any,
    tokens: tuple[str, ...],
    found: dict[tuple[int, _Slot], _Target],
) -> None:
    """沿 tokens 递归下钻，把命中的字符串登记到 found。"""
    if not tokens:
        return
    head, rest = tokens[0], tokens[1:]

    # 列表上的非通配 token 隐式穿透（等价于省略了一个 “*”）
    if isinstance(node, list) and head != "*":
        for item in node:
            _collect_path(item, tokens, found)
        return

    if isinstance(node, list):  # 此处 head 必为 “*”
        children = enumerate(node)
    elif not isinstance(node, dict):
        return
    elif head == "*":
        children = node.items()
    elif head in node:
        children = [(head, node[head])]
    else:
        return

    for slot, child in children:
        if rest:
            _collect_path(child, rest, found)
        elif _is_claimable_text(child):
            found.setdefault((id(node), slot), _Target(node, slot, child))


def _preview_budgets(
    texts: list[str], *, per_budget: int, total_budget: int
) -> tuple[int, ...]:
    """给每段文本分配 preview 字符预算（水位填充 / max-min 公平分配）。

    每段最多拿 per_budget；总和不超过 total_budget 时全额放行。超出时
    短文本优先全额满足，省下的额度回流给长文本平分，除不尽的余数补发给较短的几项。
    """
    wants = [min(len(text), per_budget) for text in texts]
    if sum(wants) <= total_budget:
        return tuple(wants)  # 总需求未超限，全额放行

    budgets = [0] * len(wants)
    remaining = total_budget
    by_want = sorted(range(len(wants)), key=wants.__getitem__)

    for rank, index in enumerate(by_want):
        unserved = len(by_want) - rank
        fair_share = remaining // unserved

        if wants[index] <= fair_share:
            budgets[index] = wants[index]
            remaining -= wants[index]
            continue

        # 当前项已超过平均份额，其后更大的需求也无法全额满足：余额平分，余数逐个 +1
        extra = remaining % unserved
        for offset, pending_index in enumerate(by_want[rank:]):
            budgets[pending_index] = fair_share + (1 if offset < extra else 0)
        break

    return tuple(budgets)


def _build_preview(text: str, budget: int) -> tuple[str, bool]:
    """生成 preview，返回 (preview, 是否发生截断)；截断时保留首尾、中间换成标记。"""
    if len(text) <= budget:
        return text, False

    if budget <= len(_TRUNCATION_MARKER):
        return text[:budget], True  # 预算过小，只保留头部

    available = budget - len(_TRUNCATION_MARKER)
    tail_len = available // 2
    head_len = available - tail_len

    tail = text[len(text) - tail_len :]
    return text[:head_len] + _TRUNCATION_MARKER + tail, True


async def _claim_one(target: _Target, budget: int, *, session_id: str) -> None:
    """寄存单个目标；仅当 preview 放不下全文时才改写输出。"""
    in_dict = isinstance(target.parent, dict)
    # 键为 content 或列表元素 / 根字符串时，字段名不带前缀
    prefix = f"{target.slot}_" if in_dict and target.slot != "content" else ""
    preview_key, id_key, length_key = (
        f"{prefix}{name}" for name in ("preview", "content_id", "total_length")
    )

    # 同级已有回执字段说明已处理过，跳过以保证幂等
    if in_dict and any(
        key in target.parent for key in (preview_key, id_key, length_key)
    ):
        return

    try:
        receipt = await put_tool_content(session_id=session_id, text=target.text)
    except Exception as exc:  # noqa: BLE001 - 缓存故障不得破坏工具主结果
        warn("tool output claim-check store failed.", e=exc)
        return
    if receipt is None:  # Store 选择不接管
        return

    preview, truncated = _build_preview(target.text, budget)
    if not truncated:  # 全文可完整展示，不向模型暴露仅供续读的回执
        return

    content_id, total_length = receipt
    target.replace_with(
        {preview_key: preview, id_key: content_id, length_key: total_length}
    )