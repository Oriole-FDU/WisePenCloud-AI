from dataclasses import dataclass
from pathlib import Path

from chat.application.tools.document.services.document_export.enums import ExportFormat
from chat.application.tools.document.services.document_export.runtime.download_resolver import DocumentDownloadResolver
from chat.domain.repositories import SessionRepository


@dataclass(frozen=True, slots=True)
class GeneratedDocumentAccess:
    file_path: Path
    file_name: str
    session_id: str
    content_type: str
    size_bytes: int


_EXTENSION_CONTENT_TYPES = {
    export_format.extension: export_format.content_type
    for export_format in ExportFormat
}


class DocumentExportDownloadService:
    def __init__(
        self,
        *,
        resolver: DocumentDownloadResolver,
        session_repo: SessionRepository,
    ) -> None:
        self._resolver = resolver
        self._session_repo = session_repo

    async def resolve_existing_file(
        self,
        *,
        download_ref: str,
        user_id: str,
    ) -> GeneratedDocumentAccess:
        resolved = self._resolver.resolve(download_ref=download_ref, user_id=user_id)
        await self._session_repo.get_by_id_and_user(resolved.session_id, user_id)

        if not resolved.file_path.is_file():
            raise FileNotFoundError("Generated document not found.")

        return GeneratedDocumentAccess(
            file_path=resolved.file_path,
            file_name=resolved.file_name,
            session_id=resolved.session_id,
            content_type=_EXTENSION_CONTENT_TYPES.get(
                resolved.file_path.suffix.lower(),
                "application/octet-stream",
            ),
            size_bytes=resolved.file_path.stat().st_size,
        )
