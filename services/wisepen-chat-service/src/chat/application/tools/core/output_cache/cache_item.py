from __future__ import annotations

import json
from typing import Any

from chat.application.tools.core.execution.result import ToolOutput
from chat.application.tools.core.output_cache.cache_base import _BaseOutputCache, _Slot


def _item_size(value: Any) -> int:
    """估算一个条目序列目标的字符规模（用于预算分配）"""
    return len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False))


class ToolOutputItemsCache(_BaseOutputCache):
    async def cache_items(self, output: ToolOutput, *, paths: tuple[str, ...], session_id: str) -> ToolOutput:
        """把条目序列（整个输出或 paths 指向的字段）超过预览预算的长字符串缓存，并替换为成条目预览"""

        # 解析 content 为 JSON，不能解析则当纯文本
        try:
            value = json.loads(output.content)
        except json.JSONDecodeError:
            value = output.content

        # 字符串 / 列表都包装成单元素列表，末尾统一 root[0] 取出
        # dict（JSON 对象）保持原样，靠 paths 定位字段
        wrapped = isinstance(value, (str, list))
        root: Any = [value] if wrapped else value

        sites = self._find_item_sites(root, paths)
        if sites:
            budgets = self._allocate_budgets([_item_size(v) for _, _, v in sites])
            for site, budget in zip(sites, budgets, strict=True):
                await self._store_item_claim(site, budget, session_id=session_id)

        result = root[0] if wrapped else root
        return ToolOutput(
            content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
            images=output.images,
        )

    def _find_item_sites(self, root: Any, paths: tuple[str, ...]) -> list[tuple[Any, _Slot, Any]]:
        """定位可作为条目序列的字符串或列表，返回 (父节点, 槽位, 值)"""

        if not paths:
            # 空 paths：整个输出是一个条目序列（已包装成 [value]）
            if isinstance(root, list) and len(root) == 1 and isinstance(root[0], (str, list)):
                return [(root, 0, root[0])]
            if isinstance(root, str):
                return [(root, 0, root)]
            return []

        # 非空 paths：在 JSON 中按路径找字符串或列表
        found: dict[tuple[int, _Slot], tuple[Any, _Slot, Any]] = {}

        def search(node: Any, tokens: tuple[str, ...]) -> None:
            head, rest = tokens[0], tokens[1:]
            if isinstance(node, list) and head != "*":
                for item in node:
                    search(item, tokens)
                return
            if isinstance(node, list):
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
                    search(child, rest)
                elif isinstance(child, (str, list)):
                    found.setdefault((id(node), slot), (node, slot, child))

        for path in paths:
            tokens = tuple(token for token in path.strip(".").split(".") if token)
            search(root, tokens)
        return list(found.values())

    async def _store_item_claim(self, site: tuple[Any, _Slot, Any], budget: int, *, session_id: str) -> None:
        """寄存一个条目序列命中点，仅当全文放不进预算时才写 Store 并改写输出"""

        parent, slot, value = site
        in_dict = isinstance(parent, dict)

        # 决定回执字段带不带前缀
        # 键为 content 或列表元素 / 根字符串时，字段名不带前缀
        prefix = f"{slot}_" if in_dict and slot != "content" else ""
        preview_key, id_key, total_key, has_more_key = (
            f"{prefix}{name}" for name in ("preview_items", "content_id", "total_items", "has_more")
        )

        # 如果父 dict 里已经存在这些回执字段，说明这个位置已经被处理过了
        if in_dict and any(key in parent for key in (preview_key, id_key, total_key, has_more_key)):
            return

        # 全文能装下就不寄存
        if _item_size(value) <= budget:
            return

        # 字符串原样存（读工具按行还原），列表存序列化后的 JSON
        store_text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        receipt = await self._store_text(store_text, session_id=session_id, is_chunk=False)
        if receipt is None:
            return

        content_id, _ = receipt
        preview, has_more, total_items = self._build_item_preview(value, budget)
        replacement = { # 组装要写回的四个字段
            preview_key: preview,
            id_key: content_id,
            total_key: total_items,
            has_more_key: has_more,
        }
        if in_dict:
            # dict 的情况，先删掉原键，再把四个回执字段作为同级键加进去
            del parent[slot]
            parent.update(replacement)
        else:
            # list 的情况，直接替换下标
            parent[slot] = replacement

    def _build_item_preview(self, value: str | list, budget: int) -> tuple[list[Any], bool, int]:
        """在预算内装尽量多的条目，返回 (preview, has_more, total_items)

        文本（str）按行切条目，超长行按字符截断；JSON（list）按元素取，元素原子返回。
        """

        if isinstance(value, str):
            items = value.splitlines()
            total = len(items)
            preview: list[Any] = []
            used = 0
            for index, line in enumerate(items):
                cost = len(line) + 1 # 粗略计入换行符
                # 已有内容且再加这行会超预算：停在整行边界，标记未完
                if preview and used + cost > budget:
                    return preview, True, total
                # 第一行本身就超预算：截断首行，保证预览非空
                if not preview and len(line) > budget:
                    return [line[:budget]], True, total
                # 整行装得下，收入预览
                preview.append(line)
                used += cost
                # 预算用满即停，当前行之后还有行才算未完
                if used >= budget:
                    return preview, index + 1 < total, total
            return preview, False, total

        # JSON 条目：原子返回，首条超预算也整条放入
        items = list(value)
        total = len(items)
        preview = []
        used = 0
        for index, item in enumerate(items):
            cost = len(json.dumps(item, ensure_ascii=False)) + 1
            if preview and used + cost > budget:
                return preview, True, total
            preview.append(item)
            used += cost
            if used >= budget:
                return preview, index + 1 < total, total
        return preview, False, total
