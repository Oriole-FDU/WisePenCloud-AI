from __future__ import annotations

from typing import List, Sequence, Tuple

from chat.application.web_search.models.common import ImageResult

__all__ = [
    "deduplicate_images",
]


def deduplicate_images(
    images: Sequence[ImageResult],
) -> Tuple[ImageResult, ...]:
    seen: set[str] = set()
    deduped: List[ImageResult] = []

    for image in images:
        key = image.url.strip()
        if not key or key in seen:
            continue

        seen.add(key)
        deduped.append(image)

    return tuple(deduped)
