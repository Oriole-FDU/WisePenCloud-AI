import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict


@contextlib.contextmanager
def _redirect_stdout_to_stderr():
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original_stdout


with _redirect_stdout_to_stderr():
    import torch

    assert torch is sys.modules.get("torch"), (
        "import torch 必须在 from paddleocr import PaddleOCR 之前执行，"
        "否则 Windows 上 paddleocr -> albumentations -> torch 的间接导入链"
        "会导致 shm.dll 加载失败 ([WinError 127])。请勿删除此 import。"
    )
    from paddleocr import PaddleOCR

    ENGINE = PaddleOCR(
        lang="ch",
        use_angle_cls=False,
        show_log=False,
    )


def _write_response(response: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _recognize_image(*, input_path: Path) -> Dict[str, Any]:
    with _redirect_stdout_to_stderr():
        raw_result = ENGINE.ocr(str(input_path), cls=False)

    lines = []

    def visit(value) -> None:
        if value is None:
            return

        if isinstance(value, tuple) and value:
            first = value[0]
            if isinstance(first, str):
                text = first.strip()
                if text:
                    lines.append(text)
                return

        if isinstance(value, list):
            if len(value) >= 2 and isinstance(value[1], tuple):
                text_value = value[1][0] if len(value[1]) >= 1 else None
                if isinstance(text_value, str):
                    text = text_value.strip()
                    if text:
                        lines.append(text)
                    return

            for item in value:
                visit(item)

    visit(raw_result)

    text = "\n".join(lines).strip()
    if not text:
        return {
            "ok": False,
            "error": "OCR_NO_TEXT",
            "message": "OCR produced no text.",
            "text": "",
        }

    return {
        "ok": True,
        "text": text,
    }


def _handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    if request.get("shutdown", False):
        return {"ok": True, "shutdown": True}

    input_value = request.get("input")
    if not input_value:
        return {"ok": False, "error": "INVALID_REQUEST", "message": "missing input"}

    input_path = Path(input_value)
    if not input_path.is_file():
        return {
            "ok": False,
            "error": "INPUT_NOT_FOUND",
            "message": f"Input image file not found: {input_path}",
        }

    try:
        return _recognize_image(input_path=input_path)
    except Exception as e:
        return {
            "ok": False,
            "error": "OCR_FAILED",
            "message": f"{e.__class__.__name__}: {e}",
        }


def _run_protocol() -> int:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue

        try:
            request = json.loads(stripped)
        except json.JSONDecodeError as e:
            _write_response({"ok": False, "error": "INVALID_JSON", "message": str(e)})
            continue

        response = _handle_request(request)
        _write_response(response)
        if response.get("shutdown"):
            return 0

    return 0


def main() -> int:
    sys.stdout = sys.stderr

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="")
    args = parser.parse_args()

    if args.input:
        _write_response(_handle_request({"input": args.input}))
        return 0

    return _run_protocol()


if __name__ == "__main__":
    raise SystemExit(main())