from __future__ import annotations

import json

from lxml import etree
from lxml.builder import ElementMaker

from .models import ContextBundle, ContextContent, ContextEvidence


_E = ElementMaker()


class ModelContextRenderer:
    """把 ContextBundle 渲染成模型可读的 XML-like 文本。"""

    __slots__ = ("name", "version")

    def __init__(self, *, version: str = "1") -> None:
        self.name = "model_context_renderer"
        self.version = version

    def render_bundle(self, bundle: ContextBundle) -> str:
        """渲染完整 bundle。"""
        root = _E(
            "context_bundle",
            schema_version="1",
            renderer=self.name,
            renderer_version=self.version,
        )

        for content in bundle.ordered_contents():
            root.append(self._content_element(content))

        if bundle.evidence:
            evidence_node = _E("evidence")
            root.append(evidence_node)
            for item in bundle.evidence:
                evidence_node.append(self._evidence_element(item))

        if bundle.assets:
            assets_node = _E("assets")
            root.append(assets_node)
            for asset in bundle.assets:
                node = _E(
                    "asset",
                    self._attrs(
                        asset_id=asset.asset_id,
                        asset_type=asset.asset_type,
                        mime_type=asset.mime_type,
                        uri=asset.uri,
                        title=asset.title,
                    ),
                )
                node.text = asset.caption or None
                assets_node.append(node)

        if bundle.actions:
            actions_node = _E("actions")
            root.append(actions_node)
            for action in bundle.actions:
                node = _E(
                    "action",
                    self._attrs(tool=action.tool, reason=action.reason, priority=action.priority),
                )
                node.text = etree.CDATA(
                    json.dumps(action.arguments, ensure_ascii=False, separators=(",", ":"))
                )
                actions_node.append(node)

        if bundle.warnings:
            warnings_node = _E("warnings")
            root.append(warnings_node)
            for warning in bundle.warnings:
                node = _E("warning")
                node.text = warning
                warnings_node.append(node)

        return etree.tostring(root, encoding="unicode", pretty_print=True).rstrip()

    def render_content(self, content: ContextContent) -> str:
        """只渲染单段正文。"""
        return etree.tostring(
            self._content_element(content),
            encoding="unicode",
            pretty_print=True,
        ).rstrip()

    def _content_element(self, content: ContextContent) -> etree._Element:
        node = _E(
            "context",
            self._attrs(
                id=content.content_id,
                kind=content.kind,
                role=content.role,
                title=content.title,
                assets=",".join(content.asset_ids),
                refs=",".join(content.ref_ids),
            ),
        )
        node.text = etree.CDATA(content.text)
        return node

    def _evidence_element(self, item: ContextEvidence) -> etree._Element:
        node = _E(
            "item",
            self._attrs(
                id=item.evidence_id,
                content_id=item.content_id,
                content_role=item.content_role,
                chunk_index=item.chunk_index,
                source_id=item.source_id,
                url=item.url,
                score=f"{item.score:.4f}" if item.score is not None else None,
                title=item.title,
            ),
        )
        if item.excerpt:
            node.text = etree.CDATA(item.excerpt)
        return node

    @staticmethod
    def _attrs(**values: object) -> dict[str, str]:
        return {key: str(value) for key, value in values.items() if value not in (None, "")}
