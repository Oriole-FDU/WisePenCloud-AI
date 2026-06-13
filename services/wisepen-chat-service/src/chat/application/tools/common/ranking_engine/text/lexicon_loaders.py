from __future__ import annotations

from pathlib import Path

from .domain_lexicon import DomainLexicon
from .lexicon_sources import ThuoclLexiconSource


DEFAULT_THUOCL_FILES: tuple[str, ...] = (
    "THUOCL_IT.txt",
    "THUOCL_caijing.txt",
    "THUOCL_chengyu.txt",
    "THUOCL_diming.txt",
    "THUOCL_law.txt",
    "THUOCL_medical.txt",
    "THUOCL_lishimingren.txt",
)


def load_thuocl_domain_lexicon(
    *,
    data_dir: Path,
    file_names: tuple[str, ...] = DEFAULT_THUOCL_FILES,
    min_frequency: int | None = 5000,
    max_terms_per_file: int | None = 10000,
    default_tag: str | None = None,
) -> DomainLexicon:
    """从本地 THUOCL data 目录加载并合并领域词典。"""
    if not data_dir.is_dir():
        raise FileNotFoundError(data_dir)

    lexicon = DomainLexicon()
    for file_name in file_names:
        file_path = data_dir / file_name
        source = ThuoclLexiconSource(
            path=file_path,
            source=f"thuocl:{file_path.stem}",
            min_frequency=min_frequency,
            max_terms=max_terms_per_file,
            default_tag=default_tag,
        )
        lexicon = lexicon.merge(source.load())

    return lexicon
