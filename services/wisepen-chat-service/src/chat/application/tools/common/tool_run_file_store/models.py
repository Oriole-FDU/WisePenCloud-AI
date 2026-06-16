from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ToolFileRefRecord:
    """工具产出短期文件的 Redis 引用记录。"""

    ref_id: str  # tfile_* 短期文件引用标识
    user_id: str  # 用户隔离键，解析时必须匹配
    session_id: str  # 会话隔离键，解析时必须匹配
    producer: str  # 产出该文件的工具或内部组件名称
    sha256: str  # 文件内容哈希，用于内容寻址和去重
    object_rel_path: str  # 相对 ToolRunFileStore 根目录的 object 路径
    filename: str  # 清洗后的展示/下载文件名
    content_type: str | None  # 上游已知 MIME，未知时为空
    size_bytes: int  # 发布时记录的文件大小，用于解析时校验
    created_at: datetime  # 引用创建时间，使用 UTC aware datetime
    expires_at: datetime  # 引用过期时间，Redis TTL 与该值保持一致
    metadata: dict[str, object] = field(default_factory=dict)  # 调用方附加的轻量元数据


@dataclass(frozen=True, slots=True)
class ResolvedToolFile:
    """已校验的短期文件引用解析结果。"""

    ref_id: str  # 已解析的 tfile_* 引用标识
    path: Path  # 已确认位于 store 根目录内的真实文件路径
    filename: str  # 清洗后的展示/下载文件名
    content_type: str | None  # 上游已知 MIME，未知时为空
    size_bytes: int  # 发布时记录的文件大小
    sha256: str  # 文件内容哈希
    producer: str  # 产出该文件的工具或内部组件名称
    expires_at: datetime  # 引用过期时间


@dataclass(frozen=True, slots=True)
class ToolRunFileCleanupResult:
    scanned_objects: int  # 扫描到的 object 文件数
    removed_objects: int  # 成功删除的过期 object 文件数
    failed_objects: int  # 删除失败或校验失败的 object 文件数
    scanned_staging_dirs: int  # 扫描到的暂存运行目录数
    removed_staging_dirs: int  # 成功删除的陈旧暂存运行目录数
    failed_staging_dirs: int  # 删除失败或校验失败的暂存运行目录数
