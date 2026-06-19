from __future__ import annotations

from pathlib import Path
from typing import Protocol, Self

from .models import LexiconSourceConfig
from ..domain_lexicon import DomainLexicon


class LexiconSource(Protocol):
    """词表数据源协议。

    定义从外部文件/数据源加载领域词表的统一接口。
    典型实现：ThuoclLexiconSource（清华大学开放中文词库）。
    """

    def load(self) -> DomainLexicon:
        """加载词表并返回领域词典。"""
        ...

    @classmethod
    def from_dir(
        cls,
        *,
        data_dir: Path,
        file_names: tuple[str, ...],
        config: LexiconSourceConfig | None = None,
    ) -> tuple[Self, ...]:
        """从目录批量构造词表数据源。

        一个目录下可能有多个词表文件（如 THUOCL 的 IT/财经/医学等），
        此方法将每个文件展开为一个独立的 LexiconSource 实例。

        Args:
            data_dir: 词表文件所在目录
            file_names: 要加载的文件名列表
            config: 通用配置，None 时由实现类提供默认值

        Returns:
            与 file_names 等长的 LexiconSource 元组
        """
        ...
