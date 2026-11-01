"""Convert OTSL table markup to HTML.

Xiaomi-OCR-0 emits tables as OTSL token streams rather than HTML:

    <fcel>Model<fcel>Re-executability<lcel><lcel><nl><fcel>Ghidra<ecel><ecel><nl>

* `<fcel>` full cell, `<ched>`/`<rhed>` header cell, `<ecel>` empty
* `<lcel>` merged into the cell on the left, `<ucel>` merged up, `<xcel>` both
* `<nl>` ends a row
"""

import html as html_module
import re
from typing import List, Optional, Tuple

# A table starts with a full/header cell and continues until a token that is not
# part of the vocabulary (or the end of the text).
_OTSL_START = re.compile(r"<(fcel|ched|rhed|srow)>")
_OTSL_TOKEN = re.compile(r"<(fcel|ched|rhed|ecel|lcel|ucel|xcel|srow|nl)>")
_CELL_TAGS = {"fcel", "ched", "rhed", "srow"}
_EMPTY_TAGS = {"ecel", "lcel", "ucel", "xcel"}


def _read_cell_text(text: str, position: int) -> Tuple[str, int]:
    """Text after a cell token runs until the next token."""
    match = _OTSL_TOKEN.search(text, position)
    end = match.start() if match else len(text)
    return text[position:end].strip(), (match.start() if match else len(text))


def looks_like_otsl(text: str) -> bool:
    return bool(_OTSL_START.search(text or ""))


def parse_otsl(text: str) -> List[List[Tuple[str, str]]]:
    """Rows of (tag, text) tokens, one entry per grid position."""
    rows: List[List[Tuple[str, str]]] = []
    row: List[Tuple[str, str]] = []
    position = 0

    while position < len(text):
        match = _OTSL_TOKEN.search(text, position)
        if not match:
            break
        tag = match.group(1)
        if tag == "nl":
            if row:
                rows.append(row)
            row = []
            position = match.end()
            continue
        cell_text, position = _read_cell_text(text, match.end())
        row.append((tag, cell_text))

    if row:
        rows.append(row)
    return rows


def otsl_to_html(text: str) -> Optional[str]:
    """Render the OTSL stream as an HTML table, honouring merges."""
    rows = parse_otsl(text)
    if not rows:
        return None

    width = max(len(row) for row in rows)
    occupied: set[Tuple[int, int]] = set()
    grid: List[List[Optional[Tuple[str, str, int, int, bool]]]] = [
        [None] * width for _ in rows
    ]

    for row_index, row in enumerate(rows):
        column = 0
        for tag, cell_text in row:
            while column < width and (row_index, column) in occupied:
                column += 1
            if column >= width:
                break

            if tag == "lcel" and column > 0:
                # Extend the cell to the left.
                origin_col = column - 1
                while origin_col > 0 and grid[row_index][origin_col] is None:
                    origin_col -= 1
                origin = grid[row_index][origin_col]
                if origin is not None:
                    span = origin[3] + 1
                    grid[row_index][origin_col] = (origin[0], origin[1], origin[2], span, origin[4])
                    occupied.add((row_index, column))
                    column += 1
                    continue

            if tag == "ucel" and row_index > 0:
                above = grid[row_index - 1][column]
                if above is not None:
                    grid[row_index - 1][column] = (
                        above[0], above[1], above[2] + 1, above[3], above[4]
                    )
                    occupied.add((row_index, column))
                    column += 1
                    continue

            is_header = tag in ("ched", "rhed", "srow")
            grid[row_index][column] = (tag, cell_text, 1, 1, is_header)
            occupied.add((row_index, column))
            column += 1

    parts = ["<table>"]
    for row_index, row in enumerate(grid):
        parts.append("<tr>")
        for column, cell in enumerate(row):
            if cell is None or (row_index, column) in occupied and cell[2] == 0:
                continue
            tag, cell_text, rowspan, colspan, is_header = cell
            # A merged cell is emitted once, at its origin.
            origin_row, origin_col = row_index, column
            if colspan > 1 and column > 0 and grid[row_index][column - 1] is not None:
                left = grid[row_index][column - 1]
                if left[2] == rowspan and left[1] == cell_text and left[3] + (column - (column - 1)) == colspan:
                    continue
            element = "th" if is_header else "td"
            attrs = ""
            if rowspan > 1:
                attrs += f' rowspan="{rowspan}"'
            if colspan > 1:
                attrs += f' colspan="{colspan}"'
            parts.append(
                f"<{element}{attrs}>{html_module.escape(cell_text, quote=False)}</{element}>"
            )
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def convert_otsl_in_markdown(text: str) -> str:
    """Replace every OTSL run in a document with an HTML table."""
    if not looks_like_otsl(text):
        return text

    output: List[str] = []
    position = 0
    while True:
        match = _OTSL_START.search(text, position)
        if not match:
            output.append(text[position:])
            break
        output.append(text[position:match.start()])
        # The run ends at the last <nl> before content that has no OTSL tokens.
        end = match.end()
        last_row_end = end
        while True:
            token = _OTSL_TOKEN.search(text, end)
            if not token:
                break
            end = token.end()
            if token.group(1) == "nl":
                last_row_end = end
        table_html = otsl_to_html(text[match.start():last_row_end])
        output.append("\n\n" + (table_html or text[match.start():last_row_end]) + "\n\n")
        position = last_row_end

    result = "".join(output)
    # A table cut off by the token budget leaves a tail of bare tokens behind.
    result = _OTSL_TOKEN.sub(" ", result)
    # The final token can be cut in half by the completion budget (`<f`, `<nl`).
    result = re.sub(r"<[a-z]{0,6}$", "", result)
    return re.sub(r"[ \t]{2,}", " ", result)
