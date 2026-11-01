"""Translate HTML tables cell by cell, preserving the markup exactly.

Sending a whole `<table>` through the translator is what corrupted tables: the
model rewrites tags, drops rowspan/colspan and collapses whitespace, so numbers
stop lining up. Here the original markup is kept byte for byte; each cell's inner
content is swapped for a placeholder, translated as a whole, and substituted back.
"""

import html as html_module
import re
from html.parser import HTMLParser
from typing import Dict, List

_TABLE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"@@C(\d+)@@")
_CELL_TAGS = {"td", "th"}


class _CellExtractor(HTMLParser):
    """Template with `@@Cn@@` inside each cell, plus the cell texts to translate."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: List[str] = []
        self.texts: List[str] = []
        self._in_cell = False
        self._cell_parts: List[str] = []
        self._cell_text: List[str] = []

    def _flush_cell(self) -> None:
        if not self._in_cell:
            return
        text = " ".join("".join(self._cell_text).split())
        if text:
            index = len(self.texts)
            self.texts.append(text)
            self.parts.append(f"@@C{index}@@")

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text() or f"<{tag}>"
        if tag in _CELL_TAGS:
            self._flush_cell()
            self.parts.append(raw)
            self._in_cell = True
            self._cell_parts = []
            self._cell_text = []
            return
        if self._in_cell:
            self._cell_parts.append(raw)
        else:
            self.parts.append(raw)

    def handle_endtag(self, tag):
        if tag in _CELL_TAGS:
            self._flush_cell()
            self._in_cell = False
            self.parts.append(f"</{tag}>")
            return
        if self._in_cell:
            self._cell_parts.append(f"</{tag}>")
        else:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self._in_cell:
            self._cell_text.append(data)
        else:
            self.parts.append(data)

    def handle_entityref(self, name):
        if self._in_cell:
            self._cell_text.append(html_module.unescape(f"&{name};"))
        else:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if self._in_cell:
            self._cell_text.append(html_module.unescape(f"&#{name};"))
        else:
            self.parts.append(f"&#{name};")


def extract_cells(table_html: str) -> tuple[str, List[str]]:
    parser = _CellExtractor()
    parser.feed(table_html)
    parser.finish() if hasattr(parser, "finish") else None
    parser.close()
    parser._flush_cell()
    return "".join(parser.parts), parser.texts


def render_cells(template: str, translations: List[str]) -> str:
    mapping: Dict[int, str] = {i: html_module.escape(text, quote=False) for i, text in enumerate(translations)}

    def replace(match: "re.Match[str]") -> str:
        return mapping.get(int(match.group(1)), match.group(0))

    return _PLACEHOLDER.sub(replace, template)


def split_tables(text: str) -> List[tuple[str, str]]:
    """Split a block into ('text'|'table', payload) segments, in order."""
    segments: List[tuple[str, str]] = []
    cursor = 0
    for match in _TABLE.finditer(text or ""):
        if match.start() > cursor:
            segments.append(("text", text[cursor:match.start()]))
        segments.append(("table", match.group(0)))
        cursor = match.end()
    if cursor < len(text or ""):
        segments.append(("text", text[cursor:]))
    return segments
