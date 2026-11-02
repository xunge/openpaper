"""PDF text-layer helpers for continuation joining.

Only one thing is needed from the PDF layer now that MinerU owns layout: whether
the previous text block ended with a hyphen, so a page/column break can join the
two halves without a space. MinerU supplies all page geometry itself.
"""

import logging
import re
from typing import Dict, List

import pymupdf

logger = logging.getLogger(__name__)


def hyphenated_block_ids(pdf_bytes: bytes, pages: List[Dict]) -> set[int]:
    """Ids of text blocks whose last PDF text line ends with a hyphen.

    Used by the page/column continuation merge: the OCR fragment drops the
    trailing hyphen, so the PDF text layer decides whether the joined word needs
    a space. One PDF open per document keeps this cheap.
    """
    result: set[int] = set()
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_data in pages:
                page_index = int(page_data.get("index", -1))
                if page_index < 0 or page_index >= doc.page_count:
                    continue
                page = doc.load_page(page_index)
                width, height = page.rect.width, page.rect.height
                text_blocks = [
                    (pymupdf.Rect(block[:4]), block[4] or "")
                    for block in page.get_text("blocks")
                    if block[6] == 0
                ]
                for block in page_data.get("blocks", []):
                    boxes = block.get("boxes_norm") or []
                    if not boxes:
                        continue
                    box = boxes[0]
                    if not isinstance(box, (list, tuple)) or len(box) != 4:
                        continue
                    rect = pymupdf.Rect(
                        box[0] * width,
                        box[1] * height,
                        box[2] * width,
                        box[3] * height,
                    )
                    best = None
                    best_overlap = 0.0
                    for text_rect, text in text_blocks:
                        inter = rect & text_rect
                        if inter.is_empty:
                            continue
                        overlap = inter.get_area() / max(rect.get_area(), 1)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best = text
                    if best is None or best_overlap < 0.3:
                        continue
                    lines = [line.strip() for line in best.splitlines() if line.strip()]
                    if lines and re.search(r"[A-Za-z]-$", lines[-1]):
                        result.add(id(block))
    except Exception as exc:
        logger.warning("reading hyphenated blocks failed: %s", exc)
    return result
