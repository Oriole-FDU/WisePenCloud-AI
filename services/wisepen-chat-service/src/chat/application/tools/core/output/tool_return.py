from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CacheableText:
    """一段需要被工具输出治理链处理的完整正文。

    这里保存的是权威原文，不是已经裁剪给模型看的 preview。输出缓存会
    先把它交给 `ToolContentStore` 持久化，再根据字符预算生成 preview；
    后续 read/search 工具通过入库返回的 `content_id` 重新读取这份原文。
    """

    text: str  # 权威正文；不能用 preview 替代，否则后续读取只能看到截断内容。
    is_md: bool = False  # 决定入库时使用 Markdown 还是纯文本 chunker。
    metadata: Mapping[str, object] = field(
        default_factory=dict
    )  # 来源等元数据会同时进入 preview 和存储实体。


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolReturn:
    """工具成功执行后的结构化输出与待治理正文。

    `visible_result` 是工具本次直接返回的业务字段；`cacheable_texts` 是
    另一条正文治理通道，允许输出缓存为长文本补充 preview、长度和后续
    `content_id`。两者职责不同，不能把长文本直接塞回 `visible_result`
    后再期待 session tools 发现它。
    """

    visible_result: Mapping[str, Any] = field(
        default_factory=dict
    )  # 工具立即可见的结构化结果。
    cacheable_texts: tuple[CacheableText, ...] = ()  # 需要预览、入库和后续读取的正文。
