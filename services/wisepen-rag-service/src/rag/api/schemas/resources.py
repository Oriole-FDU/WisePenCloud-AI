from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: NonEmptyText


class ResourceContentRequest(ResourceRequest):
    locator_name: NonEmptyText | None = None
    start: int | None = None
    end: int | None = None

    @model_validator(mode="after")
    def _validate_locator_or_range(self) -> "ResourceContentRequest":
        if self.locator_name is not None:
            if self.start is not None or self.end is not None:
                raise ValueError("locator_name cannot be combined with start/end")
        return self
