from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain_lexicon import DomainLexicon, DomainTerm


@dataclass(frozen=True, slots=True)
class ThuoclLexiconSource:
    """本地 THUOCL txt 词表数据源。"""

    path: Path  # THUOCL 词表路径
    source: str = "thuocl"  # 来源名称
    min_frequency: int | None = None  # 最低 DF / 词频阈值
    max_terms: int | None = None  # 最多读取多少词
    default_tag: str | None = None  # 默认标签

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

                if self.max_terms is not None and len(terms) >= self.max_terms:
                    break

        return DomainLexicon.from_terms(terms)

    def _parse_line(self, line: str) -> DomainTerm | None:
        """解析一行 THUOCL 词表"""
        parts = line.split("\t")
        text = parts[0].strip() if parts else ""
        if not text:
            return None

        frequency: int | None = None

        if len(parts) >= 2 and parts[1].strip():
            frequency = int(parts[1].strip())

        if self.min_frequency is not None:
            if frequency is None or frequency < self.min_frequency:
                return None

        return DomainTerm(
            text=text,
            frequency=frequency,
            tag=self.default_tag,
            source=self.source,
        )
