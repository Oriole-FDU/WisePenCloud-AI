from pathlib import Path
from types import SimpleNamespace

import pytest

from chat.application.utils import file_type_detect


class _Magika:
    def identify_path(self, file_path: Path) -> SimpleNamespace:
        return _result(label="text", mime_type="text/plain")

    def identify_bytes(self, content: bytes) -> SimpleNamespace:
        return _result(label="text", mime_type="text/plain")


def _result(*, label: str, mime_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        output=SimpleNamespace(label=label, mime_type=mime_type),
    )


@pytest.mark.parametrize(
    ("name", "extension"),
    (
        ("REPORT.PDF", "pdf"),
        ("sample.env.example", "example"),
        ("README", ""),
    ),
)
def test_detect_file_type_normalizes_last_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    extension: str,
) -> None:
    monkeypatch.setattr(file_type_detect, "_magika", _Magika())
    file_path = tmp_path / "download"
    file_path.write_bytes(b"content")

    detected = file_type_detect.detect_file_type(
        file_path,
        fallback_name=name,
    )

    assert detected.extension == extension
    assert detected.label == "text"
    assert detected.mime_type == "text/plain"


def test_detect_file_type_from_bytes_keeps_fallback_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_type_detect, "_magika", _Magika())

    detected = file_type_detect.detect_file_type_from_bytes(
        b"content",
        fallback_name="table.XLSX",
    )

    assert detected.extension == "xlsx"
