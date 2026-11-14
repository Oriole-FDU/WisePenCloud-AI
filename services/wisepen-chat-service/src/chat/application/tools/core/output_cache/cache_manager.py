from __future__ import annotations

import json
from typing import Any

from chat.application.tools.core.definition import OutputCacheStrategy
from chat.application.tools.core.execution.result import ToolOutput
from chat.application.tools.core.output_cache.cache_store import ToolContentStore
from chat.core.config.app_settings import settings
from common.logger import warn

_TRUNCATION_MARKER = "\n...[truncated]...\n"

_Container = dict[str, Any] | list[Any]  # 可被原地改写的父节点
_Slot = str | int  # dict 的键 / list 的下标

class ToolOutputCache:
    def __init__(self, *, tool_content_store: ToolContentStore) -> None:
        self._per_char_budget = settings.tools.content.preview_per_char_budget # 单段限制
        self._total_char_budget = settings.tools.content.preview_total_char_budget # 总限制
        if self._per_char_budget < 1: raise ValueError("per_char_budget must be greater than 0")
        if self._total_char_budget < 1: raise ValueError("total_char_budget must be greater than 0")

        self._store = tool_content_store

    async def cache_tool_output(self,
        output: ToolOutput,
        *,
        strategy: OutputCacheStrategy,
        session_id: str | None,
        paths: tuple[str, ...] = (),
    ) -> ToolOutput:
        if not session_id: return output
        # 不透明输出
        if strategy is OutputCacheStrategy.OPAQUE:
            return await self.cache_long_output(output, session_id=session_id)
        # 长文本
        if strategy is OutputCacheStrategy.LONG_TEXT:
            return await self.cache_long_texts(output, paths=paths, session_id=session_id)
        return output

    async def cache_long_texts(self, output: ToolOutput, *, paths: tuple[str, ...], session_id: str) -> ToolOutput:
        """把 paths 指向的超过预览预算的长字符串缓存，并替换为成缩略文本"""

        # 解析 content 为 JSON，不能解析则当纯文本
        try:
            json_output = json.loads(output.content)
        except json.JSONDecodeError:
            json_output = None

        value = json_output if json_output is not None else output.content

        # 原地替换需要父容器而字符串没有，包装为单元素字符串列表，末尾 root[0] 再取出
        root = [value] if isinstance(value, str) else value

        sites = self._find_cacheable_sites(root, paths)
        if sites: # 存在可寄存的字符串
            budgets = self._allocate_budgets([text for _, _, text in sites])
            for site, budget in zip(sites, budgets, strict=True):
                await self._store_claim(site, budget, session_id=session_id)

        result = root[0] if isinstance(value, str) else root
        return ToolOutput(
            content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
            images=output.images,
        )

    async def cache_long_output(self, output: ToolOutput, *, session_id: str) -> ToolOutput:
        """把整份不透明输出当一段文本缓存，并替换为成缩略文本"""
        text = output.content
        if len(text) <= self._per_char_budget: return output

        receipt = await self._store_text(text, session_id=session_id, is_chunk=False)
        if receipt is None: return output

        content_id, total_length = receipt
        return ToolOutput(
            content=json.dumps({
                "preview": self._build_preview(text, self._per_char_budget),
                "content_id": content_id,
                "total_length": total_length,
            }, ensure_ascii=False),
            images=output.images,
        )


    def _find_cacheable_sites(self, root: _Container, paths: tuple[str, ...]) -> list[tuple[_Container, _Slot, str]]:
        """按 paths 定位可寄存的字符串，返回 (父节点, 键/下标, 文本)。"""

        if not paths: # 字符串或字符串列表时可不提供 paths
            if not isinstance(root, list): return [] # root 不是 list，说明不是字符串（会转为一个元素的字符串列表）也不是字符串列表，直接返回空
            # 把根列表第一层里所有非空白字符串挑出来，连同它们的下标，打包成三元组返回
            return [(root, index, item) for index, item in enumerate(root) if isinstance(item, str) and item.strip()]

        # 以 (父节点 id, 槽位) 去重：多条路径命中同一字符串只记一次，且保持发现顺序
        found: dict[tuple[int, _Slot], tuple[_Container, _Slot, str]] = {}

        def search(node: Any, tokens: tuple[str, ...]) -> None:
            head, rest = tokens[0], tokens[1:] # 拆出当前这一步要匹配的token和剩下的

            # 列表上的非通配 token 隐式穿透
            # 例如路径写 results.text，但当前 results 是 list，text 显然不是 list 的键
            # 此时对列表每个元素都用同一个 token 再试一次，等价于中间自动补了个 *
            if isinstance(node, list) and head != "*":
                for item in node: search(item, tokens)
                return

            if isinstance(node, list): # node 是 list
                children = enumerate(node)

            elif not isinstance(node, dict): # node 既不是 list 也不是 dict（是字符串/数字/None），没法再往下钻，返回
                return

            elif head == "*": # node 是 dict，token 是 * 意味着取所有键值对
                children = node.items()

            elif head in node: # dict 里有这个键，只取这一个值
                children = [(head, node[head])] # 写成单元素列表 [(head, value)] 是为了和上面统一

            else: # dict 里没有这个键，静默跳过
                return

            for slot, child in children: # 遍历选出来的子节点
                if rest: search(child, rest) # 还有 token，递归往下一层
                elif isinstance(child, str) and child.strip(): # token 走完了，且当前 child 是非空白字符串，说明命中
                    # setdefault 保证同一个位置已经被别的路径记过，就保留第一次的、不覆盖
                    found.setdefault((id(node), slot), (node, slot, child)) # 记录 `(父节点, 槽位, 文本)`

        for path in paths:
            # 去掉首尾多余的点，然后按点切为 tokens
            tokens = tuple(token for token in path.strip(".").split(".") if token)
            search(root, tokens)
        return list(found.values()) # 把去重后的结果按发现顺序输出

    def _allocate_budgets(self, texts: list[str]) -> tuple[int, ...]:
        """给每段文本分配 preview 预算
        每段最多拿 _per_char_budget；总和不超过 _total_char_budget 时全部放行
        超出时短文本优先全额满足，省下的额度回流给长文本平分，除不尽的余数补发给较短几项
        """
        wants = [min(len(text), self._per_char_budget) for text in texts]

        if sum(wants) <= self._total_char_budget:
            return tuple(wants)  # 总需求未超限，全额放行

        # 总预算不够时，先按“每段想要多少预算”从小到大排序，短文本会优先拿到完整预览
        budgets = [0] * len(wants)
        remaining = self._total_char_budget
        by_want = sorted(range(len(wants)), key=wants.__getitem__)

        for rank, index in enumerate(by_want):
            unserved = len(by_want) - rank
            fair_share = remaining // unserved

            if wants[index] <= fair_share:
                # 当前段的“理想预算”仍然在公平份额以内，先完整满足它
                budgets[index] = wants[index]
                remaining -= wants[index]
                continue

            # 当前项已超过平均份额，其后更大的需求也无法全额满足
            # 余额平分，余数逐个加一
            extra = remaining % unserved
            for offset, pending_index in enumerate(by_want[rank:]):
                budgets[pending_index] = fair_share + (1 if offset < extra else 0)
            break
        return tuple(budgets)

    async def _store_claim(self, site: tuple[_Container, _Slot, str], budget: int, *, session_id: str) -> None:
        """寄存一个命中点，仅当 preview 放不下全文时才写 Store 并改写输出"""

        parent, slot, text = site
        in_dict = isinstance(parent, dict)

        # 决定回执字段带不带前缀
        # 键为 content 或列表元素 / 根字符串时，字段名不带前缀
        prefix = f"{slot}_" if in_dict and slot != "content" else ""
        preview_key, id_key, length_key = (
            f"{prefix}{name}" for name in ("preview", "content_id", "total_length")
        )

        # 如果父 dict 里已经存在这三个回执字段中的任意一个，说明这个位置已经被处理过了
        if in_dict and any(key in parent for key in (preview_key, id_key, length_key)):
            return

        # 先判断是否截断，全文能装下就不寄存
        if len(text) <= budget:
            return

        receipt = await self._store_text(text, session_id=session_id, is_chunk=True)
        if receipt is None:
            return

        content_id, total_length = receipt
        replacement = { # 组装要写回的三个字段
            preview_key: self._build_preview(text, budget),
            id_key: content_id,
            length_key: total_length,
        }
        if in_dict:
            # dict 的情况，先删掉原键，再把三个回执字段作为同级键加进去
            del parent[slot]
            parent.update(replacement)
        else:
            # list 的情况，直接替换下标
            parent[slot] = replacement

    def _build_preview(self, text: str, budget: int) -> str:
        """按 budget 截断文本：头尾各留一部分，中间放截断标记"""
        if budget <= len(_TRUNCATION_MARKER):
            return text[:budget]  # 预算小到连截断标记都放不下，就只留头部
        available = budget - len(_TRUNCATION_MARKER) # 去掉标记后还能放多少字
        tail_len = available // 2 # 尾拿一半，头拿剩下（奇数时头比尾多 1）
        return text[: available - tail_len] + _TRUNCATION_MARKER + text[len(text) - tail_len :]

    async def _store_text(
        self,
        text: str,
        *,
        session_id: str,
        is_chunk: bool,
    ) -> tuple[str, int] | None:
        """把全文写进 Store，返回 (content_id, total_length)
        Store 不接管或失败时返回 None
        """
        try:
            receipt = await self._store.put_tool_content(
                session_id=session_id, text=text, is_chunk=is_chunk
            )
        except Exception as exc:  # noqa: BLE001 - 缓存故障不得破坏工具主结果
            warn("tool output claim-check store failed.", e=exc)
            return None
        if receipt is None:  # Store 选择不接管（比如空白、超最大长度），一样跳过
            return None
        return receipt

