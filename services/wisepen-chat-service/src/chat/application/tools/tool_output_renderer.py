from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from re import fullmatch
from typing import Any
from xml.sax.saxutils import escape

from dicttoxml import dicttoxml
from pydantic import BaseModel

from chat.application.tools.core.execution.result import ToolExecutionResult
from chat.application.tools.core.llm.renderer import RenderToolResult
from chat.application.tools.core.tool_return import ToolReturn

_DEFAULT_ROOT_TAG = "result"
_XML_NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_.-]*"


@dataclass(frozen=True, slots=True)
class RenderedToolOutput:
    """工具输出完成模型渲染后的中间结果。"""
    tool_name: str
    tool_call_id: str
    tool_arguments: dict[str, Any]
    root_tag: str
    visible_result: dict[str, Any]
    cacheable_texts: tuple[str, ...]
    rendered_text: str


class ToolOutputRenderer:
    """将任意工具返回值转为模型可读 XML。"""

    __slots__ = ()  # 全静态方法，禁止意外添加实例属性

    @staticmethod
    def render_result(*, tool_result: ToolExecutionResult) -> RenderedToolOutput:
        """渲染成功输出，不做缓存占位。"""
        inv = tool_result.tool_invocation
        output = tool_result.tool_output

        if isinstance(output, ToolReturn):
            # ToolReturn 显式携带 tag / visible_result / cacheable_texts
            root_tag = _validate_xml_tag(output.tag)
            visible_result = _normalize_mapping(output.visible_result)
            cacheable_texts = tuple(str(t) for t in output.cacheable_texts)
            rendered_text = render_tool_xml(root_tag=root_tag, payload=visible_result)
        else:
            # 普通返回值：dict/list 保留结构；标量直接作为根节点文本
            root_tag = _DEFAULT_ROOT_TAG
            visible_result, rendered_text = _regular_return_parts(root_tag=root_tag, value=output)
            cacheable_texts = ()

        return RenderedToolOutput(
            tool_name=inv.tool_name,
            tool_call_id=inv.tool_call_id,
            tool_arguments=inv.tool_call_arguments,
            root_tag=root_tag,
            visible_result=visible_result,
            cacheable_texts=cacheable_texts,
            rendered_text=rendered_text,
        )

    @staticmethod
    def render_error_result(*, tool_result: ToolExecutionResult) -> RenderToolResult:
        """渲染失败输出。错误不进入 ToolContentStore。"""
        inv = tool_result.tool_invocation
        return RenderToolResult(
            tool_call_id=inv.tool_call_id,
            tool_name=inv.tool_name,
            persisted_output_placeholder=None,
            tool_output=render_tool_xml(
                root_tag=_DEFAULT_ROOT_TAG,
                payload=_error_payload(tool_result),
            ),
        )


def render_tool_xml(
    *,
    root_tag: str,
    payload: dict[str, Any],
    inline_contents: tuple[str, ...] = (),
    content_receipts: tuple[dict[str, Any], ...] = (),
) -> str:
    """将 payload 序列化为 XML，可选地在根节点末尾注入 contents / content_receipt 子节点。"""
    root_tag = _validate_xml_tag(root_tag)
    xml = dicttoxml(
        payload,
        custom_root=root_tag,
        attr_type=False,             # 不生成 type 属性（如 <foo type="str">）
        item_func=lambda _: "item",  # 列表元素统一用 <item> 包裹
        xml_declaration=False,
    ).decode("utf-8")

    # 运行时托管内容追加到根节点末尾，顺序固定：contents 先于 content_receipt
    children = ""
    if inline_contents:
        children += _render_contents(inline_contents)
    if content_receipts:
        children += _render_content_receipts(content_receipts)
    if not children:
        return xml
    return _append_root_children(xml=xml, root_tag=root_tag, children=children)


# ---------------------------------------------------------------------------
# 私有工具函数
# ---------------------------------------------------------------------------

def _regular_return_parts(*, root_tag: str, value: Any) -> tuple[dict[str, Any], str]:
    """普通返回值 → (visible_result, XML)。

    dict / list 走 dicttoxml；标量直接写根节点文本，visible_result 留空 {}。
    """
    normalized = _normalize(value)
    if isinstance(normalized, dict):
        return normalized, render_tool_xml(root_tag=root_tag, payload=normalized)
    if isinstance(normalized, list):
        payload = {"items": normalized}              # 列表 → <items><item>…</item></items>
        return payload, render_tool_xml(root_tag=root_tag, payload=payload)
    # 标量路径：root_tag 由调用方保证合法，无需重复校验
    if normalized is None:
        return {}, f"<{root_tag}/>"
    text = ("true" if normalized else "false") if isinstance(normalized, bool) else str(normalized)
    return {}, f"<{root_tag}>{escape(text)}</{root_tag}>"


def _normalize_mapping(value: Any) -> dict[str, Any]:
    """将任意 Mapping 深度递归标准化为纯 dict。"""
    return _normalize(dict(value))  # type: ignore[tool_return-value]  # _normalize(dict) 始终返回 dict


def _normalize(value: Any) -> Any:
    """递归将任意值标准化为 JSON 兼容基础类型。"""
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]  # tuple 统一转为 list
    if isinstance(value, str | int | float | bool) or value is None:
        return value                                  # 基础类型直接透传
    return str(value)                                 # 其余类型 fallback 为字符串


def _error_payload(tool_result: ToolExecutionResult) -> dict[str, Any]:
    """构造错误 payload；tool_execution_error 缺失时用 unknown_tool_error 占位。"""
    error = tool_result.tool_execution_error
    if error is None:
        # 兜底：调用链丢失了原始异常（理论上不应出现）
        return {"error": {"reason": "unknown_tool_error", "detail_reason": None, "retryable": False, "metadata": {}}}
    return _normalize_mapping({
        "error": {
            "reason": error.reason,
            "detail_reason": error.detail_reason,
            "retryable": error.retryable,
            "metadata": error.metadata,
        }
    })


def _render_contents(contents: tuple[str, ...]) -> str:
    """<contents>：单条省略 <item> 包装层，多条逐项包裹。"""
    if len(contents) == 1:
        return f"<contents>{_cdata(contents[0])}</contents>"
    items = "".join(f"<item>{_cdata(c)}</item>" for c in contents)
    return f"<contents>{items}</contents>"


def _render_content_receipts(receipts: tuple[dict[str, Any], ...]) -> str:
    """<content_receipt>：单条直接作为根，多条用 items 包裹。"""
    payload = receipts[0] if len(receipts) == 1 else {"items": list(receipts)}
    return dicttoxml(
        payload,
        custom_root="content_receipt",
        attr_type=False,
        item_func=lambda _: "item",
        xml_declaration=False,
    ).decode("utf-8")


def _append_root_children(*, xml: str, root_tag: str, children: str) -> str:
    """在根节点末尾注入子节点字符串。

    空根节点 <tag/> 直接展开；否则 rfind 最后的 </tag> 插入（防止嵌套同名标签误匹配）；
    </tag> 找不到时兜底重建。
    """
    if xml == f"<{root_tag}/>":
        return f"<{root_tag}>{children}</{root_tag}>"
    closing_tag = f"</{root_tag}>"
    index = xml.rfind(closing_tag)
    if index < 0:
        return f"<{root_tag}>{children}</{root_tag}>"
    return f"{xml[:index]}{children}{xml[index:]}"


def _cdata(text: str) -> str:
    """包裹为 CDATA 节；']]>' 须拆分为两段避免提前关闭。"""
    return f"<![CDATA[{text.replace(']]>', ']]]]><![CDATA[>')}]]>"


def _validate_xml_tag(tag: str) -> str:
    """校验 XML 标签名合法性，不合法则抛 ValueError。"""
    if not tag or fullmatch(_XML_NAME_PATTERN, tag) is None:
        raise ValueError(f"Invalid XML root tag: {tag!r}")
    return tag