from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FetchQuality:
    """抓取质量判断结果，驱动 fallback 决策。

    仅承载决策语义：
    - usable: 当前结果是否可直接作为成功结果返回
    - should_fallback: 是否应触发降级链下一层
    - reason: 机器可读的判断原因（供日志与 warning 使用）
    - text_length: 清洗后正文长度（供阈值判断与日志使用）
    """

    usable: bool
    should_fallback: bool
    reason: str
    text_length: int


@dataclass(frozen=True, slots=True)
class WebFetchResult:
    """单页抓取成功结果。

    只承载对模型决策有用的语义字段：
    - source_url / final_url: 模型需要知道原始请求与最终落地 URL
    - status_code / content_type: 模型需要判断资源性质
    - title / markdown: 模型消费的核心内容（HTML 页面路径）
    - file_ref / file_label: 非 HTML 文件移交 ToolRunFileStore 后的引用（模型可转交 document_parse）
    - warnings: 影响模型决策的提示（如降级发生、正文截断、反爬疑似）

    两种互斥结果：
    - HTML 页面：title/markdown 有值，file_ref 为 None
    - 非 HTML 文件：file_ref/file_label 有值，title/markdown 为 None
    """

    source_url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    title: str | None
    markdown: str | None
    warnings: tuple[str, ...] = ()
    file_ref: str | None = None
    file_label: str | None = None
    source_scope: str | None = None


@dataclass(frozen=True, slots=True)
class WebFetchFailure:
    """单页抓取失败结果。

    只承载失败事实，不表达重试策略（重试语义由 ToolExecutionError 承载）：
    - url: 失败的目标 URL
    - reason: 机器可读失败原因
    - detail: 人类可读失败详情（模型可转述给用户）
    """

    url: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class WebFetchBatchResult:
    """批量抓取结果。

    只承载成功结果与失败理由两类语义，外加影响决策的 warnings。
    不含审计字段（如耗时、fetcher 统计、中间过程）。
    """

    items: tuple[WebFetchResult, ...] = ()
    failed: tuple[WebFetchFailure, ...] = ()
    warnings: tuple[str, ...] = ()
