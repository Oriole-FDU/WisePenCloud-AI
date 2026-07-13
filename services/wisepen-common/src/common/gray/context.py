from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping

from common.core.constants import CommonConstants

_gray_context: ContextVar[str] = ContextVar("gray_context", default="")
_process_default_developer_tag = ""


def normalize_developer_tag(value: Any) -> str:
    raw = value[0] if isinstance(value, (list, tuple)) and value else value
    if not isinstance(raw, str):
        return ""
    return raw.strip()


class GrayContextHolder:
    @staticmethod
    def set_developer_tag(tag: Any) -> None:
        _gray_context.set(normalize_developer_tag(tag))

    @staticmethod
    def get_developer_tag() -> str:
        return normalize_developer_tag(_gray_context.get()) or _process_default_developer_tag

    @staticmethod
    def clear() -> None:
        _gray_context.set("")

    @staticmethod
    def extract_developer_tag(headers: Mapping[str, Any] | None) -> str:
        if not headers:
            return ""
        return normalize_developer_tag(headers.get(CommonConstants.GRAY_HEADER_DEV_KEY))

    @staticmethod
    def capture() -> str:
        return GrayContextHolder.get_developer_tag()

    @staticmethod
    def restore(tag: Any) -> None:
        GrayContextHolder.set_developer_tag(tag)

    @staticmethod
    def build_outbound_headers(developer_tag: Any | None = None) -> dict[str, str]:
        developer = (
            normalize_developer_tag(developer_tag)
            if developer_tag is not None
            else GrayContextHolder.get_developer_tag()
        )
        return {CommonConstants.GRAY_HEADER_DEV_KEY: developer} if developer else {}

    @staticmethod
    def build_nacos_metadata(
        base_metadata: Mapping[str, str] | None = None,
        *,
        developer_tag: Any | None = None,
    ) -> dict[str, str]:
        metadata = dict(base_metadata or {})
        developer = (
            normalize_developer_tag(developer_tag)
            if developer_tag is not None
            else GrayContextHolder.get_developer_tag()
        )
        if developer:
            metadata[CommonConstants.GRAY_METADATA_DEV_KEY] = developer
        return metadata

    @staticmethod
    def set_process_default_developer_tag(tag: Any) -> None:
        global _process_default_developer_tag
        _process_default_developer_tag = normalize_developer_tag(tag)

    @staticmethod
    def developer_of_instance(instance: Any) -> str:
        metadata = getattr(instance, "metadata", None) or {}
        return normalize_developer_tag(metadata.get(CommonConstants.GRAY_METADATA_DEV_KEY))

    @staticmethod
    def select_instance_pool(instances: list[Any]) -> list[Any]:
        developer = GrayContextHolder.get_developer_tag()
        baseline = [
            instance
            for instance in instances
            if not GrayContextHolder.developer_of_instance(instance)
        ]

        if developer:
            matched = [
                instance
                for instance in instances
                if GrayContextHolder.developer_of_instance(instance) == developer
            ]
            if matched:
                return matched
            return baseline

        return baseline
