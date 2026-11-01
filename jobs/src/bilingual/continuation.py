"""Join paragraphs that a page break split in two.

Readers care about the paragraph, not the physical page: a sentence cut at the
bottom of page N and resumed at the top of page N+1 should read (and translate)
as one block. The merge happens before translation, so the translator sees the
whole paragraph instead of two fragments.

The decision is textual, not positional: OCR boxes are often missing on scans,
and a figure or table sitting between the two fragments must not block the merge.
Structural blocks (headings, captions, lists, tables) are never merged.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HEADING_LABELS = {"title", "sub_title", "section", "heading"}
MEDIA_LABELS = {"image", "figure", "picture", "chart", "table", "equation"}

_HEADING_TEXT = re.compile(r"^\s{0,3}#{1,6}\s+")
_LIST_START = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_ORDERED_ITEM = re.compile(r"^\s*(\d{1,3})([.)])\s+")
_ORDERED_LINE = re.compile(r"(?m)^\s*(\d{1,3})[.)]\s+")
_TERMINAL = (".", "!", "?", "。", "！", "？", ":", "：", ";", "；")
_TRAILING = "\"'”’）)]】》"


def _is_heading(block: Dict[str, Any]) -> bool:
    text = (block.get("text") or "").strip()
    if block.get("label") in HEADING_LABELS:
        return True
    if _HEADING_TEXT.match(text):
        return True
    # "REFERENCES", "APPENDIX A": a short all-caps line is a section marker.
    return len(text) < 80 and text.isupper() and any(c.isalpha() for c in text)


def _is_body(block: Dict[str, Any]) -> bool:
    text = (block.get("text") or "").strip()
    if not text:
        return False
    if block.get("label") not in (None, "", "text"):
        return False
    if block.get("image_key") or "<table" in text.lower():
        return False
    if _is_heading(block) or _LIST_START.match(text):
        return False
    return True


def _is_structural(block: Dict[str, Any]) -> bool:
    """Blocks that mark a new section: never merge across them."""
    return _is_heading(block)


def _first_body(blocks: List[Dict[str, Any]]) -> Optional[Tuple[int, Dict[str, Any]]]:
    for index, block in enumerate(blocks):
        if _is_structural(block):
            return None
        if _is_body(block):
            return index, block
    return None


def _last_body(blocks: List[Dict[str, Any]]) -> Optional[Tuple[int, Dict[str, Any]]]:
    for index in range(len(blocks) - 1, -1, -1):
        block = blocks[index]
        if _is_structural(block):
            return None
        if _is_body(block):
            return index, block
    return None


def _continues(previous: Dict[str, Any], following: Dict[str, Any]) -> bool:
    """Whether `following` is the rest of `previous`'s paragraph."""
    left = (previous.get("text") or "").rstrip()
    right = (following.get("text") or "").lstrip()
    if len(left) < 80 or len(right) < 40:
        return False
    if _LIST_START.match(right) or right.startswith("#"):
        return False
    stem = left.rstrip(_TRAILING)
    if stem.endswith(_TERMINAL):
        return False
    if stem.endswith(("-", "\u00ad")) and right[:1].islower():
        return True
    # A paragraph that runs over a page break almost always resumes in lower
    # case; a capitalised next block is a new paragraph far more often.
    return right[:1].islower()


def _column_box(block: Dict[str, Any]) -> Optional[List[float]]:
    boxes = block.get("boxes_norm") or []
    if not boxes:
        return None
    box = boxes[0]
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if box[2] - box[0] > 0.6:
        # A full-width block (title, abstract, wide figure) is not part of a column.
        return None
    return [float(v) for v in box]


def _column_entries(
    blocks: List[Dict[str, Any]], side: str
) -> List[Tuple[int, Dict[str, Any]]]:
    entries: List[Tuple[int, Dict[str, Any]]] = []
    for index, block in enumerate(blocks):
        box = _column_box(block)
        if box is None:
            continue
        centre = (box[0] + box[2]) / 2
        if side == "left" and centre < 0.47 and box[2] < 0.55:
            entries.append((index, block))
        elif side == "right" and centre > 0.53 and box[0] > 0.45:
            entries.append((index, block))
    return entries


def last_column_body(blocks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Last body block of the left column, or None when it is not a paragraph."""
    for _index, block in reversed(_column_entries(blocks, "left")):
        if _is_structural(block):
            return None
        if _is_body(block):
            return block
    return None


def _ordered_numbers(text: str) -> List[int]:
    return [int(number) for number in _ORDERED_LINE.findall(text or "")]


def _ordered_item(block: Dict[str, Any]) -> Optional[tuple[int, str]]:
    if _is_heading(block) or block.get("image_key"):
        return None
    text = (block.get("text") or "").strip()
    match = _ORDERED_ITEM.match(text)
    if not match:
        return None
    if "<table" in text.lower():
        return None
    return int(match.group(1)), text


def merge_ordered_list_blocks(pages: List[Dict[str, Any]]) -> int:
    """Join consecutive numbered list items into one Markdown list block.

    Every block is rendered by its own Markdown parser, so "2. …" in a separate
    block starts a second list and appears as "1." in the reader. Merging the
    items before translation also lets the translator see 1., 2., 3. in order.
    Items are merged only when the number increases by one and the block is not a
    heading; a restarted list stays a separate group.
    """
    # Simpler and index-safe: merge in place from the end of each run.
    merged = 0
    for page in pages:
        blocks = page.get("blocks", [])
        index = 0
        while index < len(blocks) - 1:
            current = _ordered_item(blocks[index])
            current_numbers = _ordered_numbers(blocks[index].get("text") or "")
            following = _ordered_item(blocks[index + 1])
            if (
                current
                and current_numbers
                and following
                and following[0] == current_numbers[-1] + 1
                and (blocks[index].get("text") or "").strip()
            ):
                blocks[index]["text"] = (
                    (blocks[index].get("text") or "").rstrip()
                    + "\n"
                    + (blocks[index + 1].get("text") or "").strip()
                )
                blocks.pop(index + 1)
                merged += 1
                # Keep the same index so a run 1,2,3 joins repeatedly.
                continue
            index += 1

    # Cross-page runs: last item of page N and first item of page N+1.
    for page_index in range(len(pages) - 1):
        left_blocks = pages[page_index].get("blocks", [])
        right_blocks = pages[page_index + 1].get("blocks", [])
        if not left_blocks or not right_blocks:
            continue
        left_item = _ordered_item(left_blocks[-1])
        left_numbers = _ordered_numbers(left_blocks[-1].get("text") or "")
        right_item = _ordered_item(right_blocks[0])
        if (
            left_item
            and left_numbers
            and right_item
            and right_item[0] == left_numbers[-1] + 1
        ):
            left_blocks[-1]["text"] = (
                (left_blocks[-1].get("text") or "").rstrip()
                + "\n"
                + (right_blocks[0].get("text") or "").strip()
            )
            right_blocks.pop(0)
            merged += 1
            logger.info(
                "merged ordered list item from page %s into page %s",
                pages[page_index + 1].get("index"),
                pages[page_index].get("index"),
            )
    return merged


def _join(left: str, right: str, hyphenated: bool = False) -> str:
    left = left.rstrip()
    if left.endswith(("-", "\u00ad")) and right[:1].islower():
        # Re-join a word hyphenated by the page break.
        return left[:-1] + right
    if hyphenated:
        # OCR dropped the trailing hyphen but the PDF still has it.
        return left + right
    return left + " " + right


def merge_column_paragraphs(
    pages: List[Dict[str, Any]], hyphenated_pages: Optional[set[int]] = None
) -> int:
    """Join a paragraph split between the left and right columns of one page.

    Two-column PDFs cut the left column at its bottom and continue at the top of
    the right column; reading the columns as separate blocks makes the sentence
    (and its translation) start over. The same textual rule as a page break is
    used, plus a column check so a new right-column paragraph is not swallowed.
    """
    hyphenated_pages = hyphenated_pages or set()
    merged = 0
    for page in pages:
        blocks = page.get("blocks", [])
        left_entries = _column_entries(blocks, "left")
        right_entries = _column_entries(blocks, "right")
        if not left_entries or not right_entries:
            continue

        left: Optional[Tuple[int, Dict[str, Any]]] = None
        for index, block in reversed(left_entries):
            if _is_structural(block):
                break
            if _is_body(block):
                left = (index, block)
                break
        right: Optional[Tuple[int, Dict[str, Any]]] = None
        for index, block in right_entries:
            if _is_structural(block):
                break
            if _is_body(block):
                right = (index, block)
                break
        if left is None or right is None:
            continue
        left_index, left_block = left
        right_index, right_block = right
        if left_index >= right_index:
            continue
        if not _continues(left_block, right_block):
            continue

        left_block["text"] = _join(
            left_block["text"],
            right_block["text"],
            hyphenated=page.get("index") in hyphenated_pages,
        )
        blocks.pop(right_index)
        merged += 1
        logger.info(
            "merged page %s left-column paragraph with right-column continuation (%s chars)",
            page.get("index"),
            len(left_block["text"]),
        )
    return merged


def merge_page_paragraphs(
    pages: List[Dict[str, Any]], hyphenated_pages: Optional[set[int]] = None
) -> int:
    """Merge cross-page paragraph fragments in place; returns the merge count.

    `pages` is the prepared page list (``blocks`` with ``text``/``label``). Only
    the text is moved: the merged block keeps its position and box on the earlier
    page, and the fragment is removed from the later page, which is exactly what
    the reader should show.
    """
    hyphenated_pages = hyphenated_pages or set()
    merged = 0
    for index in range(len(pages) - 1):
        left_page, right_page = pages[index], pages[index + 1]
        left = _last_body(left_page.get("blocks", []))
        right = _first_body(right_page.get("blocks", []))
        if left is None or right is None:
            continue

        left_block = left[1]
        right_index, right_block = right
        if not _continues(left_block, right_block):
            continue

        left_block["text"] = _join(
            left_block["text"],
            right_block["text"],
            hyphenated=left_page.get("index") in hyphenated_pages,
        )
        right_page["blocks"].pop(right_index)
        merged += 1
        logger.info(
            "merged page %s last paragraph with page %s first paragraph (%s chars)",
            left_page.get("index"),
            right_page.get("index"),
            len(left_block["text"]),
        )
    return merged
