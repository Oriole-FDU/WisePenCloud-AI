from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


type Metadata = dict[str, Any]


class ContextContentKind(StrEnum):
    """上下文正文类型。"""

    MARKDOWN = "markdown"  # Markdown 正文
    TEXT = "text"  # 纯文本正文
    JSON = "json"  # JSON 字符串正文
    HTML = "html"  # HTML 正文


class ContextContentRole(StrEnum):
    """上下文正文角色。"""

    TOOL_RESULT = "tool_result"  # 工具原始结果
    CONTENT_REFERENCE = "content_reference"  # 可读取内容引用
    EVIDENCE = "evidence"  # 证据摘要
    WINDOW = "window"  # 读取后的正文窗口
    PAYLOAD = "payload"  # 结构化 payload 渲染结果


class ContextContent(BaseModel):
    """模型可读的一段正文。"""

    content_id: str  # 当前内容片段 ID；可以是 ToolContentStore 的 content_id
    text: str  # 模型可读正文
    kind: str = ContextContentKind.MARKDOWN  # 正文格式
    role: str = ContextContentRole.TOOL_RESULT  # 正文角色
    order: int = 0  # 渲染顺序，越小越靠前
    title: str = ""  # 可选标题
    asset_ids: tuple[str, ...] = ()  # 关联资产 ID
    ref_ids: tuple[str, ...] = ()  # 关联来源引用 ID
    metadata: Metadata = Field(default_factory=dict)  # 业务附加信息，不默认完整渲染


class ContextAsset(BaseModel):
    """文件、图片、下载物等非正文资产。"""

    asset_id: str  # 资产 ID，如 file_ref / download_ref
    asset_type: str  # 资产类型，如 file/image/download
    mime_type: str = ""  # MIME 类型
    uri: str | None = None  # 可访问 URI 或下载地址
    title: str = ""  # 资产标题
    caption: str = ""  # 给模型看的简短说明
    metadata: Metadata = Field(default_factory=dict)  # 资产附加信息


class ContextRef(BaseModel):
    """可选来源引用，用于后续 citation/provenance。"""

    ref_id: str  # 来源引用 ID
    source_type: str  # 来源类型，如 url/file/document
    source_id: str  # 来源系统内 ID
    title: str = ""  # 来源标题
    locator: Metadata = Field(default_factory=dict)  # 页码、URL、chunk 等定位信息


class ContextEvidence(BaseModel):
    """证据定位摘要，不代表完整正文。"""

    evidence_id: str  # 证据 ID
    title: str = ""  # 证据标题
    excerpt: str = ""  # 摘要片段，不是完整正文
    content_id: str | None = None  # 可读取正文的 content_id
    content_role: str | None = None  # content_id 对应内容角色
    chunk_index: int | None = None  # 命中的 chunk 序号
    source_id: str | None = None  # 外部来源 ID
    url: str | None = None  # 来源 URL
    score: float | None = None  # 相关性分数
    metadata: Metadata = Field(default_factory=dict)  # 证据附加信息


class ContextAction(BaseModel):
    """建议或要求模型后续调用的动作。"""

    tool: str  # 工具名称
    arguments: Metadata = Field(default_factory=dict)  # 工具调用参数
    reason: str = ""  # 为什么需要该动作
    priority: str = ""  # 可选优先级


class ContentPayloadManifest(BaseModel):
    """单段正文的 payload 摘要。"""

    content_id: str
    role: str
    kind: str
    chars: int
    title: str = ""


class ContextRenderManifest(BaseModel):
    """上下文渲染 manifest，用于记录本次渲染的结构摘要。"""

    schema_version: int = 1
    renderer: str = "model_context_renderer"
    renderer_version: str = "1"
    storage_mode: str = "inline"
    payload_chars: int = 0
    contents: tuple[ContentPayloadManifest, ...] = ()
    assets: tuple[ContextAsset, ...] = ()
    refs: tuple[ContextRef, ...] = ()


class ContextBundle(BaseModel):
    """模型上下文的结构化中间形态。"""

    contents: tuple[ContextContent, ...] = ()  # 模型可读正文集合
    assets: tuple[ContextAsset, ...] = ()  # 非正文资产集合
    refs: tuple[ContextRef, ...] = ()  # 可选来源引用
    evidence: tuple[ContextEvidence, ...] = ()  # 证据定位摘要
    actions: tuple[ContextAction, ...] = ()  # 后续动作建议
    warnings: tuple[str, ...] = ()  # 告警或限制说明
    metadata: Metadata = Field(default_factory=dict)  # bundle 级附加信息

    def payload_chars(self) -> int:
        """统计真正进入模型正文的字符量，不包含 XML 外壳。"""
        return sum(len(content.text) for content in self.contents)

    def ordered_contents(self) -> tuple[ContextContent, ...]:
        """按 order 稳定排序正文片段。"""
        return tuple(
            content
            for _, content in sorted(
                enumerate(self.contents),
                key=lambda item: (item[1].order, item[0]),
            )
        )

    def manifest(self, *, storage_mode: str = "inline") -> ContextRenderManifest:
        """生成当前 bundle 的渲染摘要。"""
        return ContextRenderManifest(
            storage_mode=storage_mode,
            payload_chars=self.payload_chars(),
            contents=tuple(
                ContentPayloadManifest(
                    content_id=content.content_id,
                    role=content.role,
                    kind=content.kind,
                    chars=len(content.text),
                    title=content.title,
                )
                for content in self.ordered_contents()
            ),
            assets=self.assets,
            refs=self.refs,
        )
