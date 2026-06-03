from pydantic import BaseModel


class UploadedChatFileResponse(BaseModel):
    file_id: str
    file_ref: str
    file_name: str
    content_type: str
    size_bytes: int
    preview_url: str
    download_url: str
