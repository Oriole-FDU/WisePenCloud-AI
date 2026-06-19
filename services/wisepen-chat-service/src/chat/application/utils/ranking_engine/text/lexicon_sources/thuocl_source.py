from __future__ import annotations

from pathlib import Path

from .models import LexiconSourceConfig
from ..domain_lexicon import DomainLexicon, DomainTerm

# THUOCL（清华大学开放中文词库）默认词表文件
# 涵盖 IT、财经、成语、地名、法律、医学、历史名人等领域
DEFAULT_THUOCL_FILES: tuple[str, ...] = (
    "THUOCL_IT.txt",
    "THUOCL_caijing.txt",
    "THUOCL_chengyu.txt",
    "THUOCL_diming.txt",
    "THUOCL_law.txt",
    "THUOCL_medical.txt",
    "THUOCL_lishimingren.txt",
)


class ThuoclLexiconSource:
    """本地 THUOCL txt 词表数据源。

    THUOCL 词表格式：每行一个词条，以 tab 分隔词条和词频，如：
        人工智能\t12345
        深度学习\t8900

    加载流程：
        1. 逐行读取文件，跳过空行和 # 开头的注释行
        2. 按 tab 分割，第一列为词条文本，第二列为词频（可选）
        3. 按 min_frequency 过滤低频词，按 max_terms 限制加载数量
        4. 转换为 DomainLexicon 供 BM25 分词器使用
    """

    __slots__ = ("path", "config")

    def __init__(
        self,
        *,
        path: Path,
        config: LexiconSourceConfig | None = None,
        source: str | None = None,
        min_frequency: int | None = None,
        max_terms: int | None = None,
        default_tag: str | None = None,
    ) -> None:
        """初始化 THUOCL 词表数据源。

        Args:
            path: 词表文件路径
            config: 预构造的配置对象，优先级高于单独参数
            source: 词表来源标识（config 为 None 时生效）
            min_frequency: 最低词频阈值（config 为 None 时生效）
            max_terms: 最大词条数（config 为 None 时生效）
            default_tag: 默认标签（config 为 None 时生效）
        """
        self.path = path
        self.config = config or LexiconSourceConfig(
            source=source or "thuocl",
            min_frequency=min_frequency,
            max_terms=max_terms,
            default_tag=default_tag,
        )

    @classmethod
    def from_dir(
        cls,
        *,
        data_dir: Path,
        file_names: tuple[str, ...] = DEFAULT_THUOCL_FILES,
        config: LexiconSourceConfig | None = None,
        source_prefix: str = "thuocl",
        min_frequency: int | None = 5000,
        max_terms_per_file: int | None = 10000,
        default_tag: str | None = None,
    ) -> tuple[ThuoclLexiconSource, ...]:
        """从 THUOCL data 目录展开多个文件 source。"""
        if not data_dir.is_dir():
            raise FileNotFoundError(data_dir)

        result: list[ThuoclLexiconSource] = []
        for file_name in file_names:
            file_path = data_dir / file_name
            source_config = config or LexiconSourceConfig(
                source=f"{source_prefix}:{file_path.stem}",
                min_frequency=min_frequency,
                max_terms=max_terms_per_file,
                default_tag=default_tag,
            )
            result.append(cls(path=file_path, config=source_config))

        return tuple(result)

    def load(self) -> DomainLexicon:
        """读取本地 THUOCL txt 文件并转换成 DomainLexicon。"""
        if not self.path.exists():
            raise FileNotFoundError(self.path)

        terms: list[DomainTerm] = []
        with self.path.open("r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#"):   # 跳过空行和注释行
                    continue

                term = self._parse_line(line)
                if term is None:
                    continue
                terms.append(term)

                if self.config.max_terms is not None and len(terms) >= self.config.max_terms:
                    break

        return DomainLexicon.from_terms(terms)

    def _parse_line(self, line: str) -> DomainTerm | None:
        """解析一行 THUOCL 词表。

        格式：词条\\t词频（词频可选）
        示例：人工智能\\t12345 → DomainTerm(text="人工智能", frequency=12345)
        """
        parts = line.split("\t")
        text = parts[0].strip() if parts else ""
        if not text:
            return None

        frequency: int | None = None

        if len(parts) >= 2 and parts[1].strip():
            raw_frequency = parts[1].strip()
            if not raw_frequency.isdigit():
                return None
            frequency = int(raw_frequency)

        if self.config.min_frequency is not None:
            if frequency is None or frequency < self.config.min_frequency:
                return None

        return DomainTerm(
            text=text,
            frequency=frequency,
            tag=self.config.default_tag,
            source=self.config.source,
        )
