from dataclasses import dataclass
from typing import List, Optional

import fitz

from chat.application.tools.document.services.document_parse.enums import PageType
from chat.application.tools.document.services.document_parse.utils.text import normalize_text

_IMAGE_BLOCK_TYPE = 1


@dataclass(slots=True)
class ImageProbe:

    has_displayed_images: bool
    area_ratio: Optional[float]
    area_calc_failed: bool = False


@dataclass(slots=True)
class PageProbe:
    page_index: int
    page_type: PageType
    text: str
    text_length: int
    has_images: bool
    image_area_ratio: Optional[float]


class PageClassifier:
    """
    PDF 页面类型分类器。

    - TEXT: 原生文本充足。
    - MIXED: 有文本，同时有图片，或少量文本但不足以视为纯文本页。
    - SCANNED: 文本不足，但图片区域占比较大。
    - EMPTY: 没有有效文本，也没有有效图片。
    """

    def __init__(
        self,
        *,
        min_text_chars: int = 30,
        image_area_ratio: float = 0.65,
    ):
        """初始化对象依赖。"""
        self._min_text_chars = min_text_chars
        self._image_area_ratio = image_area_ratio

    def probe_page(self, page: fitz.Page, *, page_index: int) -> PageProbe:
        """
        主探测路径。

        - 优先使用 get_text("blocks") 同时拿文本块和图片块。
        - 文本很少时，再调用 get_image_info / get_images 做图片面积兜底探测。
        """
        # blocks 同时包含文本块和图片块，是低成本的第一页判断入口。
        try:
            blocks = page.get_text("blocks", sort=True) or []
        except (RuntimeError, ValueError, TypeError):
            blocks = []

        # 从文本 block 中提取原生文本。
        text = self._text_from_blocks(blocks)

        # 先基于 blocks 估算图片显示面积。
        image_probe = self._image_probe_from_blocks(blocks=blocks, page=page)

        # 文本不足时，页面可能是扫描页；再走更完整的图片探测。
        if len(text) < self._min_text_chars:
            image_probe = self._probe_images(
                page=page,
                fallback_has_images=image_probe.has_displayed_images,
            )

        # 文本长度 + 图片面积共同决定页面类型。
        page_type = self._determine_page_type(text=text, image_probe=image_probe)

        return PageProbe(
            page_index=page_index,
            page_type=page_type,
            text=text,
            text_length=len(text),
            has_images=image_probe.has_displayed_images,
            image_area_ratio=image_probe.area_ratio,
        )

    def fallback_probe_page(self, page: fitz.Page, *, page_index: int) -> PageProbe:
        """
        fallback 探测路径。

        - 用 page.get_text("text") 直接抽文本。
        - 图片探测失败时保守保留 area_calc_failed 状态。
        """
        # fallback 文本抽取不依赖 blocks。
        try:
            text = normalize_text(page.get_text("text", sort=True))
        except (RuntimeError, ValueError, TypeError):
            text = ""

        # 图片探测属于 PyMuPDF 第三方边界，失败时保留失败状态。
        try:
            image_probe = self._probe_images(page=page)
        except (RuntimeError, ValueError, TypeError):
            image_probe = ImageProbe(
                has_displayed_images=False,
                area_ratio=None,
                area_calc_failed=True,
            )

        page_type = self._determine_page_type(text=text, image_probe=image_probe)

        return PageProbe(
            page_index=page_index,
            page_type=page_type,
            text=text,
            text_length=len(text),
            has_images=image_probe.has_displayed_images,
            image_area_ratio=image_probe.area_ratio,
        )

    def _determine_page_type(self, *, text: str, image_probe: ImageProbe) -> PageType:
        # 文本充足时，优先视为原生文本页。
        """处理当前流程。"""
        if len(text) >= self._min_text_chars:
            if image_probe.has_displayed_images:
                return PageType.MIXED
            return PageType.TEXT

        # 文本不足但图片占比高，属于典型扫描页。
        if (
            image_probe.area_ratio is not None
            and image_probe.area_ratio >= self._image_area_ratio
        ):
            return PageType.SCANNED

        # 图片面积算不出，但确认有图片时，保守按扫描页处理。
        if image_probe.area_ratio is None and image_probe.has_displayed_images:
            return PageType.SCANNED

        # 有少量文本但不够成文本页，归为混合页。
        if text:
            return PageType.MIXED

        return PageType.EMPTY


    def _text_from_blocks(self, blocks: List[object]) -> str:
        """处理当前流程。"""
        parts: List[str] = []

        for block in blocks:
            # PyMuPDF block[6] 是 block 类型；图片 block 不参与文本拼接。
            if int(block[6]) == _IMAGE_BLOCK_TYPE:
                continue

            # PyMuPDF block[4] 是文本内容。
            text = block[4].strip()
            if text:
                parts.append(text)

        return normalize_text("\n".join(parts))

    def _image_probe_from_blocks(
        self,
        *,
        blocks: List[object],
        page: fitz.Page,
    ) -> ImageProbe:
        """处理当前流程。"""
        page_area = float(page.rect.width * page.rect.height)
        image_area = 0.0
        has_images = False

        for block in blocks:
            # 只统计图片 block 的 bbox 面积。
            if int(block[6]) != _IMAGE_BLOCK_TYPE:
                continue

            has_images = True

            # block 前四项是 bbox: x0, y0, x1, y1。
            width = float(block[2]) - float(block[0])
            height = float(block[3]) - float(block[1])

            if width > 0 and height > 0:
                image_area += width * height

        if not has_images:
            return ImageProbe(
                has_displayed_images=False,
                area_ratio=0.0,
                area_calc_failed=False,
            )

        return ImageProbe(
            has_displayed_images=True,
            area_ratio=image_area / page_area if page_area > 0 else 0.0,
            area_calc_failed=False,
        )

    def _probe_images(
        self,
        *,
        page: fitz.Page,
        fallback_has_images: bool = False,
    ) -> ImageProbe:
        # 首选 get_image_info：能直接拿 displayed image bbox。
        """处理当前流程。"""
        try:
            infos = page.get_image_info(hashes=False, xrefs=False)
        except (RuntimeError, ValueError, TypeError):
            infos = None

        if infos is not None:
            if not infos:
                return ImageProbe(
                    has_displayed_images=False,
                    area_ratio=0.0,
                    area_calc_failed=False,
                )

            image_area = 0.0
            page_area = float(page.rect.width * page.rect.height)

            for info in infos:
                bbox = info.get("bbox")
                if not bbox or len(bbox) < 4:
                    continue

                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]

                if width > 0 and height > 0:
                    image_area += float(width * height)

            return ImageProbe(
                has_displayed_images=True,
                area_ratio=image_area / page_area if page_area > 0 else 0.0,
                area_calc_failed=False,
            )

        # get_image_info 不可用时，退回 get_images + get_image_rects。
        has_images = fallback_has_images or bool(page.get_images(full=True))
        if not has_images:
            return ImageProbe(
                has_displayed_images=False,
                area_ratio=0.0,
                area_calc_failed=False,
            )

        try:
            image_area = 0.0

            # get_images 拿嵌入图片对象，get_image_rects 拿页面显示区域。
            for image in page.get_images(full=True):
                xref = image[0]
                for rect in page.get_image_rects(xref):
                    image_area += float(rect.width * rect.height)

            page_area = float(page.rect.width * page.rect.height)
            return ImageProbe(
                has_displayed_images=True,
                area_ratio=image_area / page_area if page_area > 0 else 0.0,
                area_calc_failed=False,
            )
        except (RuntimeError, ValueError, TypeError):
            return ImageProbe(
                has_displayed_images=True,
                area_ratio=None,
                area_calc_failed=True,
            )
