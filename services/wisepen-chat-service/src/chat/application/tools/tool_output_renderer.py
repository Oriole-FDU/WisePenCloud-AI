from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from re import fullmatch
from typing import Any
from xml.sax.saxutils import escape

from dicttoxml import dicttoxml

# 禁用 dicttoxml 的调试日志
import logging
logging.getLogger("dicttoxml").setLevel(logging.WARNING)
from lxml import etree
from pydantic import BaseModel

from chat.application.tools.core.execution.result import ToolExecutionResult
from chat.application.tools.core.llm.renderer import RenderToolResult
from chat.application.tools.core.tool_return import ToolReturn

# --- XML 规范化配置 ---
_DEFAULT_ROOT_TAG = "result"
_XML_NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_.-]*"
_XML_PARSER = etree.XMLParser(strip_cdata=False)


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

        # 场景 A: 工具显式返回高度结构化的 ToolReturn
        if isinstance(output, ToolReturn):
            root_tag = _validate_xml_tag(output.tag)
            visible_result = _normalize_mapping(output.visible_result)
            cacheable_texts = tuple(str(t) for t in output.cacheable_texts)

            rendered_text = render_tool_xml(
                root_tag=root_tag,
                payload=visible_result,
            )

        # 场景 B: 工具返回原生普通类型 (dict/list/标量)
        else:
            root_tag = _DEFAULT_ROOT_TAG
            visible_result, rendered_text = _regular_return_parts(
                root_tag=root_tag,
                value=output,
            )
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
        """渲染失败输出。错误内容不计入 ToolContentStore 缓存。"""
        inv = tool_result.tool_invocation

        return RenderToolResult(
            tool_call_id=inv.tool_call_id,
            tool_name=inv.tool_name,
            persisted_output_placeholder=None,
            tool_output=render_tool_xml(
                root_tag=_DEFAULT_ROOT_TAG,
                payload=_error_payload(tool_result),
            ),
            debug_output=_error_payload(tool_result),
        )


def render_tool_xml(
        *,
        root_tag: str,
        payload: dict[str, Any],
        inline_contents: tuple[str, ...] = (),
        content_receipts: tuple[dict[str, Any], ...] = (),
) -> str:
    """底层的核心 XML 构建器，调度 dicttoxml 完成序列化并注入相关子节点。"""
    root_tag = _validate_xml_tag(root_tag)

    xml = dicttoxml(
        payload,
        custom_root=root_tag,
        attr_type=False,
        item_func=lambda _: "item",
        xml_declaration=False,
    ).decode("utf-8")

    # 动态组装并注入附加的业务子内容节点
    children = ""
    if inline_contents:
        children += _render_contents(inline_contents)
    if content_receipts:
        children += _render_content_receipts(content_receipts)

    if children:
        xml = _append_root_children(
            xml=xml,
            root_tag=root_tag,
            children=children,
        )

    return _prettify(xml)


# ---------------------------------------------------------------------------
# 私有工具辅助函数 (Private Helper Functions)
# ---------------------------------------------------------------------------

def _regular_return_parts(*, root_tag: str, value: Any) -> tuple[dict[str, Any], str]:
    """处理普通非 ToolReturn 的原生返回值。

    映射与列表走传统的序列化树分支；标量数据则直接充当根节点文本。
    """
    normalized = _normalize(value)

    if isinstance(normalized, dict):
        return normalized, render_tool_xml(root_tag=root_tag, payload=normalized)

    if isinstance(normalized, list):
        payload = {"items": normalized}  # 封装为标准列表节点: <items><item>...</item></items>
        return payload, render_tool_xml(root_tag=root_tag, payload=payload)

    # 纯标量特化路径：直接注入纯文本 (注意安全转义)
    if normalized is None:
        return {}, f"<{root_tag}/>"

    text = ("true" if normalized else "false") if isinstance(normalized, bool) else str(normalized)
    return {}, f"<{root_tag}>{escape(text)}</{root_tag}>"


def _normalize_mapping(value: Any) -> dict[str, Any]:
    """将任意 Mapping 深度递归标准化为纯基础 dict。"""
    return _normalize(dict(value))  # type: ignore[tool_return-value]


def _normalize(value: Any) -> Any:
    """递归将任意异构数据、框架模型转换标准化为 JSON/XML 兼容的基础类型。"""
    if isinstance(value, Enum):
        return _normalize(value.value)

    if isinstance(value, BaseModel):
        return _normalize(value.model_dump())

    if is_dataclass(value) and not isinstance(value, type):
        return _normalize(asdict(value))

    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]  # 元组统一降维为标准列表

    if isinstance(value, str | int | float | bool) or value is None:
        return value  # 基础标量直接放行透传

    return str(value)  # 无法匹配的奇异类一律降级退化为字符串


def _error_payload(tool_result: ToolExecutionResult) -> dict[str, Any]:
    """构造标准化的工具执行错误 XML 载荷。"""
    error = tool_result.tool_execution_error

    if error is None:
        # 并发或分发链条丢失了原始异常时的严苛防御兜底
        return {
            "error": {
                "reason": "unknown_tool_error",
                "detail_reason": None,
                "retryable": False,
                "metadata": {},
            }
        }

    return _normalize_mapping({
        "error": {
            "reason": error.reason,
            "detail_reason": error.detail_reason,
            "retryable": error.retryable,
            "metadata": error.metadata,
        }
    })


def _render_contents(contents: tuple[str, ...]) -> str:
    """渲染富文本大字段内容块。单条免除冗余包装，多条使用 item 隔离。"""
    if len(contents) == 1:
        return f"<contents>{_cdata(contents[0])}</contents>"

    items = "".join(f"<item>{_cdata(c)}</item>" for c in contents)
    return f"<contents>{items}</contents>"


def _render_content_receipts(receipts: tuple[dict[str, Any], ...]) -> str:
    """渲染内容回执单据列表。"""
    payload = receipts[0] if len(receipts) == 1 else {"items": list(receipts)}

    return dicttoxml(
        payload,
        custom_root="content_receipt",
        attr_type=False,
        item_func=lambda _: "item",
        xml_declaration=False,
    ).decode("utf-8")


def _append_root_children(*, xml: str, root_tag: str, children: str) -> str:
    """在现存的 XML 根节点最内层尾部，安全注入子节点字符串。

    使用反向查找（rfind）定位闭合根标签，精确规避子树中同名嵌套标签引发的污染。
    """
    if xml == f"<{root_tag}/>":
        return f"<{root_tag}>{children}</{root_tag}>"

    closing_tag = f"</{root_tag}>"
    index = xml.rfind(closing_tag)

    if index < 0:
        return f"<{root_tag}>{children}</{root_tag}>"

    return f"{xml[:index]}{children}{xml[index:]}"


def _cdata(text: str) -> str:
    """将高风险明文包裹为 XML CDATA 节。

    内部若存在恶意或天然的 ']]>' 须执行截断重构，防止 XML 节点提前被迫关闭崩溃。
    """
    escaped_text = text.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{escaped_text}]]>"


def _validate_xml_tag(tag: str) -> str:
    """严苛校验 XML 标签的命名规范合法性。"""
    if not tag or fullmatch(_XML_NAME_PATTERN, tag) is None:
        raise ValueError(f"Invalid XML root tag: {tag!r}")
    return tag


def _prettify(xml: str) -> str:
    """利用 lxml 解析树将杂乱的一线式 XML 格式化为携带标定缩进的模型易读版。"""
    root = etree.fromstring(xml.encode(), _XML_PARSER)
    return etree.tostring(root, pretty_print=True, encoding="unicode")
