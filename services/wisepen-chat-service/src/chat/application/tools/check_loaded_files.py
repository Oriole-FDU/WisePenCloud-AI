import json
from dataclasses import dataclass
from typing import Any


LoadedFileKey = tuple[str, str]


def _already_loaded_response(
    *,
    message: str,
    file_type: str,
    file_id: str,
    file_path: str,
) -> str:
    return json.dumps(
        {
            "message": message,
            "file_type": file_type,
            "file_id": file_id,
            "file_path": file_path,
        },
        ensure_ascii=False,
    )


@dataclass(frozen=True)
class LoadedFileCheckResult:
    already_loaded: bool
    response: str | None
    file_type: str
    file_id: str
    file_path: str
    _loaded_key: LoadedFileKey
    _turn_loaded: set[LoadedFileKey]
    _recorded_turn: bool

    def rollback(self) -> None:
        if self._recorded_turn:
            self._turn_loaded.discard(self._loaded_key)


def _check_and_record(
    *,
    turn_loaded: set[LoadedFileKey],
    loaded_key: LoadedFileKey,
    file_type: str,
    file_id: str,
    file_path: str,
    message: str,
) -> LoadedFileCheckResult:
    already_loaded = loaded_key in turn_loaded
    if already_loaded:
        return LoadedFileCheckResult(
            already_loaded=True,
            response=_already_loaded_response(
                message=message,
                file_type=file_type,
                file_id=file_id,
                file_path=file_path,
            ),
            file_type=file_type,
            file_id=file_id,
            file_path=file_path,
            _loaded_key=loaded_key,
            _turn_loaded=turn_loaded,
            _recorded_turn=False,
        )

    turn_loaded.add(loaded_key)
    return LoadedFileCheckResult(
        already_loaded=False,
        response=None,
        file_type=file_type,
        file_id=file_id,
        file_path=file_path,
        _loaded_key=loaded_key,
        _turn_loaded=turn_loaded,
        _recorded_turn=True,
    )


def check_and_record_loaded_file(
    *,
    context: dict[str, Any],
    file_type: str,
    file_id: str,
    file_path: str,
    message: str,
) -> LoadedFileCheckResult:
    """
    Check whether a file has already been loaded in the current turn, and record it
    atomically when allowed. Cross-turn visibility is derived from the assembled
    prompt, not from process-local session state.
    """
    loaded_key = (file_type, file_id)
    turn_loaded = context.setdefault("turn_loaded_files", set())

    return _check_and_record(
        turn_loaded=turn_loaded,
        loaded_key=loaded_key,
        file_type=file_type,
        file_id=file_id,
        file_path=file_path,
        message=message,
    )
