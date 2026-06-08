from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class ContentChunk:
    """
    持久化文本对象的切片元数据模型，用于大模型上下文限制下的精确滑动窗定位。
    - index: 切片在整个文本对象中的序号
    - start_offset: 当前切片在原始文本中的字符起始偏移量
    - end_offset: 当前切片在原始文本中的字符结束偏移量
    - token_count: 当前切片经过分词器（Tokenizer）计算后的 Token 数量
    - metadata: 预留给特定上游工具的切片级自定义辅助元数据字典
    """
    index: int
    start_offset: int
    end_offset: int
    token_count: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StoredContent:
    """
    通用基础设施层持久化存储的工具内容实体（网盘/虚拟内存对象映射）。
    - content_id: 全局唯一的内容资产标识符
    - scope_id: 会话域隔离标识符，通常与具体的会话 ID 强绑定
    - producer: 生产该数据的上游工具名称，解耦具体的业务场景
    - source: 原始数据源的抽象描述文本（如多路搜索的 queries 拼接串）
    - content_type: 存储文本的媒体类型格式（如 application/json）
    - text: 实际存储的核心原始文本内容（如 Evidence Pack 的 JSON 串）
    - chunks: 经基础设施层切片算法分割后的 ContentChunk 元数据列表
    - metadata: 全局级自定义随路元数据字典，用于向后传导控制信息
    """
    content_id: str
    scope_id: str
    producer: str
    source: str
    content_type: str
    text: str
    chunks: List[ContentChunk] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContentWindow:
    """
    下游消费端读取持久化内容时返回的虚拟滑动视窗切片对象。
    - content_id: 所属内容资产的全局唯一标识符
    - producer: 生产该数据的原始上游工具名称
    - source: 原始数据源的描述信息
    - content_type: 内容的媒体类型格式
    - original_length: 原始未切片文本的总字符长度
    - chunk_index: 当前视窗所包含的切片起始索引序号
    - chunk_count: 当前视窗所覆盖的切片总数量
    - offset: 当前视窗在原始文本中的绝对字符起始偏移量
    - returned_length: 当前视窗实际返回的文本字符长度
    - truncated: 当前视窗是否因为触及读取上限而发生了截断的标记
    - next_offset: 若触发截断，下一次接续读取的绝对字符偏移量
    - text: 当前视窗读取并拼接出来的核心文本段落
    - error: 基础设施层在读取或切片时发生的阻塞型硬错误描述
    - cached: 目标资产是否成功命中持久化缓存系统的标记
    - cache_error: 持久化缓存层发生的非阻塞型软异常描述
    - warning: 基础设施层留给下游消费端的审计或容错告警提示信息
    - metadata: 随路透传而来的全局级自定义元数据字典
    """
    content_id: str
    producer: str
    source: str
    content_type: str
    original_length: int
    chunk_index: int = 0
    chunk_count: int = 1
    offset: int = 0
    returned_length: int = 0
    truncated: bool = False
    next_offset: Optional[int] = None
    text: str = ""
    error: Optional[str] = None
    cached: bool = True
    cache_error: Optional[str] = None
    warning: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)




@dataclass(slots=True)
class ContentReceipt:
    """
    上游工具成功将资产写入持久化存储后，由基础设施层颁发的数据存证凭据（凭证证件）。
    - content_id: 由存储系统生成的全局唯一资产存证 ID
    - producer: 生产该存证资产的上游工具名称
    - source: 该资产对应的原始数据源描述
    - content_type: 资产内容的媒体类型格式
    - original_length: 被持久化的核心文本总字符长度
    - chunk_count: 被持久化资产经切片后生成的切片总数
    - cached: 资产是否已安全持久化写入缓存系统的标记
    - cache_error: 写入持久化缓存层时发生的非阻塞型软异常描述
    - error: 导致持久化存证彻底失败的阻塞型硬错误描述
    - warning: 持久化写入过程中产生的非阻断性告警或留痕提示
    - metadata: 随路绑定并持久化的全局级自定义元数据字典
    """
    content_id: str
    producer: str
    source: str
    content_type: str
    original_length: int
    chunk_count: int
    cached: bool = True
    cache_error: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
