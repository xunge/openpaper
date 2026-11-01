"""Rebuild well-formed HTML tables from what OCR emits.

DeepSeek-OCR writes `<table><td>…<td>…</table>`: no rows, no closing tags. Browsers
hoist those cells out of the table, so nothing renders as a grid. Cells and their
span attributes are parsed here and re-emitted inside explicit `<tr>` elements,
with the column count inferred from the header row's spans.
"""

import html as html_module
import logging
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TABLE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)


class _Cell:
    __slots__ = ("text", "rowspan", "colspan", "tag")

    def __init__(self, tag: str, attrs: Dict[str, str]) -> None:
        self.tag = tag
        self.text = ""
        self.rowspan = max(1, _int(attrs.get("rowspan"), 1))
        self.colspan = max(1, _int(attrs.get("colspan"), 1))


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.cells: List[_Cell] = []
        self._current: Optional[_Cell] = None
        self._rows = 0

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._rows += 1
        elif tag in ("td", "th"):
            self._current = _Cell(tag, dict(attrs))
            self.cells.append(self._current)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current.text += data

    def handle_entityref(self, name):
        if self._current is not None:
            self._current.text += html_module.unescape(f"&{name};")

    def handle_charref(self, name):
        if self._current is not None:
            self._current.text += html_module.unescape(f"&#{name};")


def _infer_columns(cells: List[_Cell]) -> int:
    """Column count from the header spans; must tile the whole cell set evenly."""
    prefix = 0
    candidates: List[int] = []
    for cell in cells:
        prefix += cell.colspan
        candidates.append(prefix)
        if len(candidates) >= 8:  # a header never needs more than a few cells
            break
    total = sum(cell.colspan for cell in cells)
    for width in sorted(set(candidates), reverse=True):
        if width > 0 and total % width == 0:
            return width
    return max(candidates) if candidates else 0


def _build_grid(cells: List[_Cell], width: int) -> List[List[Tuple[int, int, _Cell]]]:
    """Place cells on a grid honouring rowspan/colspan; returns rows of spans."""
    occupied: set[Tuple[int, int]] = set()
    placed: Dict[Tuple[int, int], _Cell] = {}
    row = 0
    col = 0

    # A cell wider than the inferred grid never fits: `col + colspan > width`
    # stays true at col 0, so the placement loop advances the row forever. OCR
    # emits exactly that when a header span is misread, so clamp before placing.
    for cell in cells:
        cell.colspan = max(1, min(cell.colspan, width))
        cell.rowspan = max(1, min(cell.rowspan, len(cells) + 1))

    for cell in cells:
        while True:
            if col >= width:
                row += 1
                col = 0
                continue
            if (row, col) in occupied:
                col += 1
                continue
            # Does the cell fit on this row?
            if col + cell.colspan > width:
                row += 1
                col = 0
                continue
            break

        for dr in range(cell.rowspan):
            for dc in range(cell.colspan):
                occupied.add((row + dr, col + dc))
        placed[(row, col)] = cell
        col += cell.colspan

    if not placed:
        return []

    rows: List[List[Tuple[int, int, _Cell]]] = []
    for r in range(max(r for r, _ in placed) + 1):
        start_col = 0
        row_cells: List[Tuple[int, int, _Cell]] = []
        while start_col < width:
            cell = placed.get((r, start_col))
            if cell is None:
                # Covered by a rowspan from above: no new <td> is emitted.
                start_col += 1
                continue
            row_cells.append((r, start_col, cell))
            start_col += cell.colspan
        rows.append(row_cells)
    return rows


def normalise_table(table_html: str) -> str:
    """Return a well-formed table, or the original markup when there is nothing."""
    collector = _Collector()
    try:
        collector.feed(table_html)
        collector.close()
    except Exception as exc:
        logger.warning("table parse failed: %s", exc)
        return table_html

    cells = [cell for cell in collector.cells if cell.text.strip()]
    if not cells:
        return table_html

    width = _infer_columns(cells)
    if width <= 0:
        return table_html

    rows = _build_grid(cells, width)
    if not rows:
        return table_html

    parts = ["<table>"]
    for row in rows:
        parts.append("<tr>")
        for _row, _col, cell in row:
            attrs = ""
            if cell.rowspan > 1:
                attrs += f' rowspan="{cell.rowspan}"'
            if cell.colspan > 1:
                attrs += f' colspan="{cell.colspan}"'
            text = html_module.escape(" ".join(cell.text.split()), quote=False)
            parts.append(f"<{cell.tag}{attrs}>{text}</{cell.tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)
