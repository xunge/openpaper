"""Ground block geometry in the PDF itself.

The OCR model's layout boxes do not scale with the rendered image (the same page
at 100 and 200 DPI returns identical coordinates), so using them for cropping and
click-to-jump lands in the wrong place. Digital-native PDFs carry an exact text
layer, so positions are recovered from there and OCR blocks are matched to it by
text overlap.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

import pymupdf

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z]{3,}")


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")}


def text_blocks(pdf_bytes: bytes, page_index: int) -> List[Dict]:
    """Text blocks of one page as {rect (points), text}, in reading order."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc.load_page(page_index)
            blocks = []
            for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
                if not text.strip():
                    continue
                blocks.append(
                    {"rect": [x0, y0, x1, y1], "text": text.strip(), "tokens": _tokens(text)}
                )
            return blocks
    except Exception as exc:
        logger.warning("reading the text layer of page %s failed: %s", page_index, exc)
        return []


def figure_rects(pdf_bytes: bytes, page_index: int) -> List[List[float]]:
    """Rects of embedded images and large vector clusters (figures)."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc.load_page(page_index)
            rects: List[List[float]] = []
            # Bitmaps, including inline ones: the text dict reports them as type 1
            # blocks, which get_images() misses for Form XObjects and inline data.
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") == 1 and block.get("bbox"):
                    rects.append([float(v) for v in block["bbox"]])
            for info in page.get_images(full=True):
                for rect in page.get_image_rects(info[0]):
                    rects.append([rect.x0, rect.y0, rect.x1, rect.y1])

            if not rects:
                # Vector figures: merge every drawing that is part of the same
                # cluster, otherwise a figure made of many small paths is cropped
                # down to whatever the first big path happened to be.
                pieces = [
                    [d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1]
                    for d in page.get_drawings()
                    if d.get("rect") and not d["rect"].is_empty
                ]
                rects = _merge_rects(pieces, gap=24)
                min_area = page.rect.width * page.rect.height * 0.04
                large = [r for r in rects if (r[2] - r[0]) * (r[3] - r[1]) > min_area]
                if large:
                    rects = large
                elif rects:
                    # A figure assembled from many scattered paths never forms one
                    # big cluster; fall back to the union of everything drawn.
                    rects = [[
                        min(r[0] for r in rects),
                        min(r[1] for r in rects),
                        max(r[2] for r in rects),
                        max(r[3] for r in rects),
                    ]]
            # The same figure is often reported twice (text dict and image list).
            deduped: List[List[float]] = []
            for rect in _merge_rects(rects, gap=6):
                if not any(
                    abs(rect[0] - d[0]) < 4 and abs(rect[1] - d[1]) < 4 and abs(rect[2] - d[2]) < 4
                    for d in deduped
                ):
                    deduped.append(rect)
            deduped.sort(key=lambda r: (round(r[1]), r[0]))
            return deduped
    except Exception as exc:
        logger.warning("reading figures of page %s failed: %s", page_index, exc)
        return []


def match_rect(block_text: str, candidates: List[Dict]) -> Optional[List[float]]:
    """Pick the candidate whose text best overlaps the OCR block."""
    tokens = _tokens(block_text)
    if not tokens or not candidates:
        return None

    best_score = 0.0
    best_rect: Optional[List[float]] = None
    for candidate in candidates:
        if not candidate["tokens"]:
            continue
        overlap = len(tokens & candidate["tokens"])
        if overlap == 0:
            continue
        # Jaccard-ish: favour candidates that cover most of the block's words.
        score = overlap / len(tokens)
        if score > best_score:
            best_score = score
            best_rect = candidate["rect"]
    return best_rect if best_score >= 0.5 else None


def normalise(rect: List[float], page_rect: Tuple[float, float]) -> List[float]:
    width, height = page_rect
    return [
        round(rect[0] / width, 5),
        round(rect[1] / height, 5),
        round(rect[2] / width, 5),
        round(rect[3] / height, 5),
    ]


def page_size(pdf_bytes: bytes, page_index: int) -> Tuple[float, float]:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        rect = doc.load_page(page_index).rect
        return rect.width, rect.height


def match_blocks_ordered(ocr_texts: List[str], candidates: List[Dict], max_window: int = 3) -> List[Optional[List[float]]]:
    """Align OCR blocks to PDF blocks in reading order.

    A single logical block is often split across two PDF text blocks (a title that
    wraps, a paragraph that crosses a column), and scoring candidates
    independently lets an unrelated paragraph win on a few shared words. Windows
    are therefore grown over *consecutive* candidates and scored by how much of
    the OCR block they cover.
    """
    results: List[Optional[List[float]]] = []
    cursor = 0
    used: set[int] = set()

    for text in ocr_texts:
        tokens = _tokens(text)
        if not tokens or cursor >= len(candidates):
            results.append(None)
            continue

        best_score = 0.0
        best_window = (cursor, cursor)
        for start in range(cursor, min(cursor + 4, len(candidates))):
            union: set[str] = set()
            union_rect: Optional[List[float]] = None
            for end in range(start, min(start + max_window, len(candidates))):
                union |= candidates[end]["tokens"]
                rect = candidates[end]["rect"]
                union_rect = rect if union_rect is None else [
                    min(union_rect[0], rect[0]),
                    min(union_rect[1], rect[1]),
                    max(union_rect[2], rect[2]),
                    max(union_rect[3], rect[3]),
                ]
                score = len(tokens & union) / len(tokens)
                if score > best_score:
                    best_score = score
                    best_window = (start, end)

        if best_score >= 0.5:
            start, end = best_window
            rect = candidates[start]["rect"]
            for index in range(start + 1, end + 1):
                other = candidates[index]["rect"]
                rect = [
                    min(rect[0], other[0]),
                    min(rect[1], other[1]),
                    max(rect[2], other[2]),
                    max(rect[3], other[3]),
                ]
            results.append(rect)
            used.update(range(start, end + 1))
            cursor = end + 1
        else:
            results.append(None)

    # Second pass: OCR and PDF block counts rarely line up, so a block that lost
    # the windowed match usually still corresponds to the next unused candidate.
    for index, text in enumerate(ocr_texts):
        if results[index] is not None:
            continue
        tokens = _tokens(text)
        if not tokens:
            continue
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used or not candidate["tokens"]:
                continue
            if len(tokens & candidate["tokens"]) / len(tokens) >= 0.25:
                results[index] = candidate["rect"]
                used.add(candidate_index)
                break

    return results


def _merge_rects(rects: List[List[float]], gap: float = 20) -> List[List[float]]:
    """Union rectangles that overlap or sit within `gap` of each other."""
    merged: List[List[float]] = []
    for rect in rects:
        current = list(rect)
        rest: List[List[float]] = []
        for other in merged:
            if (
                current[0] - gap <= other[2]
                and other[0] - gap <= current[2]
                and current[1] - gap <= other[3]
                and other[1] - gap <= current[3]
            ):
                current = [
                    min(current[0], other[0]),
                    min(current[1], other[1]),
                    max(current[2], other[2]),
                    max(current[3], other[3]),
                ]
            else:
                rest.append(other)
        rest.append(current)
        merged = rest
    return merged


def _grow(rect: "pymupdf.Rect", gap: float) -> "pymupdf.Rect":
    return pymupdf.Rect(rect.x0 - gap, rect.y0 - gap, rect.x1 + gap, rect.y1 + gap)


def _cluster_regions(
    items: List[Tuple["pymupdf.Rect", bool]], gap: float = 8.0
) -> List["pymupdf.Rect"]:
    """Group drawing/image rects into figure candidates.

    White-filled rects are common figure backgrounds. They are used as connectors
    (so a bar chart's separate bars form one cluster) but never contribute to the
    returned bbox: a full-page white panel would otherwise swallow the header and
    surrounding text.
    """

    clusters: List[Dict] = []
    for rect, white in sorted(items, key=lambda item: (round(item[0].y0, 1), item[0].x0)):
        for cluster in clusters:
            if _grow(cluster["all"], gap).intersects(rect):
                cluster["all"] = cluster["all"] | rect
                if not white:
                    cluster["ink"] = (
                        rect if cluster["ink"] is None else cluster["ink"] | rect
                    )
                break
        else:
            clusters.append(
                {
                    "all": pymupdf.Rect(rect),
                    "ink": None if white else pymupdf.Rect(rect),
                }
            )

    # Chaining: a cluster created later may bridge two earlier ones.
    changed = True
    while changed:
        changed = False
        merged: List[Dict] = []
        for cluster in clusters:
            for other in merged:
                if _grow(other["all"], gap).intersects(cluster["all"]):
                    other["all"] = other["all"] | cluster["all"]
                    if cluster["ink"] is not None:
                        other["ink"] = (
                            cluster["ink"]
                            if other["ink"] is None
                            else other["ink"] | cluster["ink"]
                        )
                    changed = True
                    break
            else:
                merged.append(cluster)
        clusters = merged

    return [cluster["ink"] for cluster in clusters if cluster["ink"] is not None]


def figure_regions(pdf_bytes: bytes, page_index: int) -> List[List[float]]:
    """Figure rectangles recovered from the PDF itself.

    Needed for OCR backends that emit no layout markers (Xiaomi): without this
    pass their reader shows the original page on the left but never a cropped
    figure inline. Raster images and vector drawings are both considered; tables
    are excluded by the caller, which knows where the OCR found `<table>` blocks.

    Returns page-space rects ``[x0, y0, x1, y1]`` in reading order.
    """
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc.load_page(page_index)
            width, height = page.rect.width, page.rect.height
            page_area = width * height
            text_rects = [
                pymupdf.Rect(block[:4])
                for block in page.get_text("blocks")
                if block[6] == 0
            ]

            items: List[Tuple["pymupdf.Rect", bool]] = []

            # Bitmaps. A full-page scan is the page background, not a figure.
            for info in page.get_image_info(xrefs=True):
                rect = pymupdf.Rect(info["bbox"]) & page.rect
                if rect.width >= 40 and rect.height >= 30 and rect.get_area() <= 0.75 * page_area:
                    items.append((rect, False))

            for drawing in page.get_drawings():
                rect = pymupdf.Rect(drawing["rect"]) & page.rect
                if rect.is_empty:
                    continue
                if rect.width > 0.95 * width and rect.height > 0.95 * height:
                    continue
                if rect.width < 2 and rect.height < 2:
                    continue
                fill = drawing.get("fill")
                white = bool(fill) and all(channel >= 0.99 for channel in fill)
                # A filled panel that is mostly covered by text is a callout box,
                # not a figure. Keeping it would merge the next figure into the
                # surrounding paragraph.
                if not white and rect.get_area() > 0.005 * page_area:
                    covered = sum((rect & t).get_area() for t in text_rects)
                    if covered / rect.get_area() > 0.45:
                        continue
                items.append((rect, white))

            # Page 1 splits into a title block and (often) a teaser figure. The
            # title's large glyphs, when outlined, cluster with that figure and
            # produce a crop containing the title and authors. Drop any region
            # that overlaps the title block.
            title_rect = None
            if page_index == 0:
                spans = [
                    pymupdf.Rect(span["bbox"])
                    for block in page.get_text("dict")["blocks"]
                    if block.get("type") == 0
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                    if span.get("size", 0) >= 14
                ]
                if spans:
                    title_rect = spans[0]
                    for span in spans[1:]:
                        title_rect = title_rect | span

            # Captions sit just below their figure; using the caption's top edge
            # keeps a multi-panel chart from being clipped at the last panel.
            captions = [
                pymupdf.Rect(block[:4])
                for block in page.get_text("blocks")
                if block[6] == 0
                and re.match(r"(?i)\s*(fig(ure)?\.?\s*\d+)", block[4] or "")
            ]

            regions: List[List[float]] = []
            for rect in _cluster_regions(items):
                if not (
                    rect.width >= 0.15 * width
                    and rect.height >= 0.05 * height
                    and rect.get_area() >= 0.02 * page_area
                ):
                    continue
                if title_rect is not None:
                    inter = rect & title_rect
                    if inter.get_area() / rect.get_area() > 0.08:
                        continue

                below = [
                    caption
                    for caption in captions
                    if 0 <= caption.y0 - rect.y1 < 100
                    and caption.x0 < rect.x1
                    and caption.x1 > rect.x0
                ]
                bottom = min((c.y0 for c in below), default=rect.y1)
                if bottom > rect.y1:
                    rect = pymupdf.Rect(rect.x0, rect.y0, rect.x1, bottom - 2)

                # Asymmetric margin: charts keep their y-axis labels on the
                # left and their title above, while the bottom is already
                # anchored to the caption and must not pull the caption in.
                pad_left = min(18.0, 0.03 * width)
                pad_top = min(12.0, 0.02 * height)
                regions.append(
                    [
                        max(0.0, rect.x0 - pad_left),
                        max(0.0, rect.y0 - pad_top),
                        min(width, rect.x1 + 8.0),
                        min(height, rect.y1 + 2.0),
                    ]
                )
            regions.sort(key=lambda r: (round(r[1]), r[0]))
            return regions
    except Exception as exc:
        logger.warning("reading figures of page %s failed: %s", page_index, exc)
        return []


def overlap_ratio(a: List[float], b: List[float]) -> float:
    """Fraction of `a` covered by `b` (both as [x0, y0, x1, y1])."""
    width = a[2] - a[0]
    height = a[3] - a[1]
    area = width * height
    if area <= 0:
        return 0.0
    inter_w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    inter_h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return (inter_w * inter_h) / area


def page_ends_hyphenated(pdf_bytes: bytes, page_index: int) -> bool:
    """Whether the body text of a page ends with a hyphenated word.

    OCR often drops the trailing hyphen (`decompi-` becomes `decompi`), so the
    page-break merge would otherwise join the halves with a space and print
    `decompi lation`. The PDF text layer still has the hyphen for digital PDFs;
    scans return False and fall back to the normal space join.
    """
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc.load_page(page_index)
            lines = [line.strip() for line in page.get_text().splitlines() if line.strip()]

        # Footers and arXiv stamps follow the last body line; drop them before
        # asking whether the paragraph itself was hyphenated. Looking at "any of
        # the last three lines" also caught a hyphenated compound earlier in the
        # paragraph and joined unrelated words without a space.
        def furniture(line: str) -> bool:
            lowered = line.lower()
            return (
                lowered.startswith(("arxiv:", "ieee ", "acm ", "proceedings"))
                or re.fullmatch(r"(page\s*)?\d+", lowered) is not None
                or len(line) < 4
            )

        while lines and furniture(lines[-1]):
            lines.pop()
        return bool(lines) and bool(re.search(r"[A-Za-z]-$", lines[-1]))
    except Exception as exc:
        logger.warning("reading the page ending of page %s failed: %s", page_index, exc)
        return False


def column_ends_hyphenated(
    pdf_bytes: bytes, page_index: int, left_bbox: Optional[List[float]] = None
) -> bool:
    """Whether the left column's last text line ends with a hyphen.

    The OCR fragment loses the trailing hyphen (`decompi-` becomes `decompi`);
    the PDF text layer still has it. The column merge must then join without a
    space, exactly like the cross-page case.
    """
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc.load_page(page_index)
            width = page.rect.width
            candidates = []
            for block in page.get_text("blocks"):
                if block[6] != 0:
                    continue
                rect = pymupdf.Rect(block[:4])
                if rect.x1 > 0.55 * width:
                    continue
                if left_bbox:
                    # Only inspect blocks that overlap the OCR block's column.
                    if rect.x1 < left_bbox[0] * width or rect.x0 > left_bbox[2] * width:
                        continue
                candidates.append((rect.y1, rect.y0, block[4] or ""))
        if not candidates:
            return False
        candidates.sort(key=lambda item: (item[0], item[1]))
        lines = [line.strip() for line in candidates[-1][2].splitlines() if line.strip()]
        return bool(lines) and bool(re.search(r"[A-Za-z]-$", lines[-1]))
    except Exception as exc:
        logger.warning(
            "reading left-column ending of page %s failed: %s", page_index, exc
        )
        return False


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
