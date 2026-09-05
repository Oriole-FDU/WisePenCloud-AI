"""工具返回值的原地 Claim Check 装饰器。

装饰器拥有模型输出前的最后一个缓存边界：它接收异构业务对象，先收敛为
JSON 纯树，再把超出预算、需要正文 Store 接管的长字符串替换成同级 preview 和
receipt 字段；未超过预算的正文保持原输出，不暴露缓存字段。
Redis、分块和文档结构仍由 ``cache_store`` 与 Redis repository 独立负责。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from common.logger import warn
from pydantic import TypeAdapter

from chat.application.tools.core.execution.result import ToolOutput
from chat.application.tools.core.output_cache.cache_store import put_tool_content

_TRUNCATION_MARKER = "\n...[truncated]...\n"
_JSON_ADAPTER = TypeAdapter(Any)


def cacheable_tool_output(
    func: Callable[..., Any] | None = None,
    *,
    paths: tuple[str, ...] = (),
) -> Callable[..., Any]:
    """把工具返回值中的字符串原地替换为 preview 和缓存回执。

    嵌套对象必须显式声明点号路径和 ``*`` 通配符，避免误缓存普通业务字段。
    """

    def decorate(target: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(target):
            raise TypeError("cacheable_tool_output requires an async function")

        @wraps(target)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 获取session id用于缓存
            context = kwargs.get("context")  # excute 由框架统一注入关键字参数执行
            if not isinstance(context, dict) or "session_id" not in context:
                raise TypeError(
                    f"cacheable_tool_output requires a valid 'context' dict with 'session_id' in kwargs, "
                    f"got {type(context).__name__}"
                )
            session_id = context["session_id"]

            # 执行工具execute函数
            raw = await target(*args, **kwargs)

            tool_output = raw if isinstance(raw, ToolOutput) else None
            result = await process_cacheable_output(
                raw.content if tool_output else raw,
                paths=paths,
                session_id=session_id,
            )

            # 如果结果依然是纯文本，说明未被寄存，或缓存存储失败降级了，按原字符串返回
            if isinstance(result, str):
                return result if tool_output is None else ToolOutput(
                    content=result,
                    images=raw.images,
                )

            # 结构化数据封装返回
            if tool_output is not None:
                return ToolOutput(
                    content=json.dumps(result, ensure_ascii=False),
                    images=raw.images,
                )
            return result

        return wrapper

    # 无 paths 时只处理根字符串，或根 list/tuple 的第一层字符串元素
    if func is None:
        return decorate
    return decorate(func)


async def process_cacheable_output(
    value: Any,
    *,
    paths: tuple[str, ...],
    session_id: str,
) -> Any:
    """在 Host 侧执行路径声明对应的 Claim Check 变异。"""

    from chat.core.config.app_settings import settings

    # MCP 信封和本地工具都先收敛成同一种 JSON 纯树，后续路径语义保持一致。
    pure = _dump_json_tree(value)
    target_root = [pure] if isinstance(pure, str) else pure
    targets = _collect_targets(target_root, paths)
    if targets:
        budgets = _preview_budgets(
            [target.text for target in targets],
            per_budget=settings.TOOL_CONTENT_PREVIEW_PER_CHAR_BUDGET,
            total_budget=settings.TOOL_CONTENT_PREVIEW_TOTAL_CHAR_BUDGET,
        )
        await _claim_targets(targets, budgets, session_id=session_id)

    return target_root[0] if isinstance(pure, str) else target_root


class _Target:
    __slots__ = ("parent", "prefix", "slot", "text")

    def __init__(self, parent: dict[str, Any] | list[Any], slot: str | int, text: str, prefix: str) -> None:
        self.parent = parent    # 父级容器（字典或列表）
        self.slot = slot        # 在父容器中的键名或列表下标
        self.text = text        # 需要缓存的长文本内容
        self.prefix = prefix    # 字段前缀，用于后续生成key


def _dump_json_tree(value: Any) -> Any:
    "将任意复杂对象展开为纯数据"
    # 若输入是 str，尝试用 json.loads 解析（例如工具直接返回了 JSON 字符串）
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return _JSON_ADAPTER.dump_python(value, mode="json")


def _collect_targets(root: Any, paths: tuple[str, ...]) -> list[_Target]:
    # 装饰器无参调用时，默认只找根节点字符串或者根列表的第一层字符串
    if not paths:
        if isinstance(root, str):
            return [_Target([root], 0, root, "")]
        if isinstance(root, list):
            return [
                _Target(root, index, value, "")
                for index, value in enumerate(root)
                if isinstance(value, str) and value.strip()
            ]
        return []

    targets: list[_Target] = []
    seen: set[tuple[int, str | int]] = set()
    for path in paths:
        tokens = tuple(token for token in path.strip(".").split(".") if token)
        _collect_path(root, tokens, targets, seen)
    return targets


def _collect_path(
    node: Any,
    tokens: tuple[str, ...],
    targets: list[_Target],
    seen: set[tuple[int, str | int]],
) -> None:
    if not tokens:
        return
    # 每次拿走第一个 token，剩下的 remaining 下钻
    token, remaining = tokens[0], tokens[1:]

    # 处理列表
    if isinstance(node, list):
        for index, item in enumerate(node):
            if token == "*":
                # token为*，且有剩余路径，则对列表中的每个容器继续递归
                if remaining:
                    _collect_path(item, remaining, targets, seen)
                # 如果没有剩余路径，只需用当前列表中的str逐个生成Target
                elif isinstance(item, str) and item.strip() and (id(node), index) not in seen:
                    seen.add((id(node), index))
                    targets.append(_Target(node, index, item, ""))
            # 隐式下钻，允许省略*，对列表中的每个容器递归
            else:
                _collect_path(item, tokens, targets, seen)
        return

    # 处理字典
    if not isinstance(node, dict):
        return

    # 字典通配分支
    # * 表示不限制键名，遍历当前字典的所有键值对
    if token == "*":
        for key, item in node.items():
            # 终点命中，路径结束
            if not remaining:
                if (
                    isinstance(item, str)
                    and item.strip()
                    and (id(node), key) not in seen
                ):
                    seen.add((id(node), key))
                    # 记录 prefix 为当前键名
                    targets.append(_Target(node, key, item, key))
            # 仍有后续路径，继续递归
            else:
                _collect_path(item, remaining, targets, seen)
        return

    # 容错机制：key不存在时退出缓存判定，不抛出错误
    if token not in node:
        return

    # 字典精确键名匹配
    if not remaining:
        value = node[token]
        if isinstance(value, str) and value.strip() and (id(node), token) not in seen:
            seen.add((id(node), token))
            targets.append(_Target(node, token, value, token))
        return

    _collect_path(node[token], remaining, targets, seen)


async def _claim_targets(
    targets: list[_Target],
    budgets: tuple[int, ...],
    *,
    session_id: str,
) -> None:
    """将目标入库，并按是否截断选择模型可见的输出形状。"""
    for target, budget in zip(targets, budgets, strict=True):
        prefix = (
            f"{target.prefix}_"
            if target.prefix and target.prefix != "content"
            else ""
        )
        claim_keys = (
            f"{prefix}preview",
            f"{prefix}content_id",
            f"{prefix}total_length",
            f"{prefix}chunk_count",
        )
        if isinstance(target.parent, dict) and any(
            key in target.parent for key in claim_keys
        ):
            continue

        try:
            # 存入缓存
            receipt = await put_tool_content(session_id=session_id, text=target.text)
        except Exception as exc:  # noqa: BLE001 - 缓存故障不得破坏工具主结果
            warn("tool output claim-check store failed.", e=exc)
            continue
        if receipt is None:
            continue
        preview, truncated = _build_preview(target.text, budget)
        if not truncated:
            # 原文已能完整展示时不改写输出，也不向模型暴露仅供续读的 receipt。
            continue
        replacement = {
            f"{prefix}preview": preview,
            f"{prefix}content_id": receipt.content_id,
            f"{prefix}total_length": receipt.total_length,
            f"{prefix}chunk_count": receipt.chunk_count,
        }
        if isinstance(target.parent, dict):
            target.parent.pop(target.slot)
            target.parent.update(replacement)
        else:
            target.parent[target.slot] = replacement


def _preview_budgets(texts: list[str], *, per_budget: int, total_budget: int) -> tuple[int, ...]:
    # 计算每段文本理想所需的字数
    desired = [min(len(text), per_budget) for text in texts]
    # 不超过预算则全额放行
    if sum(desired) <= total_budget:
        return tuple(desired)

    budgets = [0] * len(desired)
    remaining = total_budget
    # 从小到大排序，有限满足短文本
    ordered = sorted(range(len(desired)), key=desired.__getitem__)

    for position, index in enumerate(ordered):
        pending = len(ordered) - position   # 剩余待分配的名额
        fair_share = remaining // pending   # 当前平均份额
        if desired[index] <= fair_share:
            budgets[index] = desired[index]
            # 满足小需求，节省额度回流预算
            remaining -= desired[index]
            continue

        # 一旦当前需求大于平均，说明后续大需求项均无法全额满足，只分配均值预算
        for pending_index in ordered[position:]:
            budgets[pending_index] = fair_share

        # 将余数逐个补发给前几项
        for pending_index in ordered[position : position + remaining % pending]:
            budgets[pending_index] += 1
        break
    return tuple(budgets)


def _build_preview(text: str, budget: int) -> tuple[str, bool]:
    if len(text) <= budget:
        return text, False
    # 极端边界防御：如果预算连标记符（\n...[truncated]...\n）都装不下，直接硬截前缀
    if budget <= len(_TRUNCATION_MARKER):
        return text[:budget], True

    # 对称切除中间，保留首尾
    available = budget - len(_TRUNCATION_MARKER)
    tail_budget = available // 2
    return text[: available - tail_budget] + _TRUNCATION_MARKER + text[-tail_budget:], True
