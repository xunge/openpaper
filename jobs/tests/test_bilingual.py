"""Regression tests for the bilingual helpers that run inside the worker.

Run from the jobs directory:

    uv run python -m unittest discover -s tests
"""

import unittest

import pymupdf

from src.bilingual.pdf_layout import (
    column_ends_hyphenated,
    figure_regions,
    page_ends_hyphenated,
)
from src.bilingual.pipeline import ensure_closed_tables
from src.bilingual.table_normalise import normalise_table
from src.bilingual.text_normalise import align_ordered_list_markers, normalise_math


class TestTableNormalise(unittest.TestCase):
    def test_oversized_colspan_does_not_spin(self):
        """A cell wider than the inferred grid used to loop forever.

        `_build_grid` advances the row whenever `col + colspan > width`; with
        colspan > width that is true at col 0 forever, which pinned a worker at
        100% CPU and froze the document at "assembling".
        """
        header = "".join(f"<td>h{i}</td>" for i in range(8))
        html = (
            f"<table><tr>{header}</tr>"
            '<tr><td colspan="20">wide</td></tr></table>'
        )
        out = normalise_table(html)
        self.assertIn("<table>", out)
        self.assertIn("wide", out)

    def test_rebuilds_rows_from_cells_without_tr(self):
        html = "<table><td>a</td><td>b</td><td>c</td><td>d</td></table>"
        out = normalise_table(html)
        self.assertIn("<tr>", out)
        self.assertIn("<td>a</td>", out)


class TestEnsureClosedTables(unittest.TestCase):
    def test_appends_missing_closer(self):
        self.assertTrue(ensure_closed_tables("<table><td>x</td>").endswith("</table>"))

    def test_leaves_closed_table_alone(self):
        html = "<table><tr><td>x</td></tr></table>"
        self.assertEqual(ensure_closed_tables(html), html)


class TestNormaliseMath(unittest.TestCase):
    def test_numbered_equation_becomes_display_block(self):
        """`$$...$$` on one line is inline math to remark-math, where \tag fails."""
        out = normalise_math("$$x \\tag{1}$$")
        self.assertIn("\n$$\n", out)
        self.assertTrue(out.strip().startswith("$$"))
        self.assertTrue(out.strip().endswith("$$"))

    def test_inline_math_is_untouched(self):
        out = normalise_math("Let $a+b$ be the sum.")
        self.assertEqual(out, "Let $a+b$ be the sum.")


class TestPageHyphenation(unittest.TestCase):
    def test_detects_trailing_hyphen(self):
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 760), "researchers have reformulated decompi-")
        self.assertTrue(page_ends_hyphenated(doc.tobytes(), 0))

    def test_plain_ending_is_not_hyphenated(self):
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 760), "researchers have reformulated the task")
        self.assertFalse(page_ends_hyphenated(doc.tobytes(), 0))


class TestColumnHyphenation(unittest.TestCase):
    def _pdf(self, ending: str) -> bytes:
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        page.insert_text((60, 760), ending)
        page.insert_text((330, 100), "right column text")
        return doc.tobytes()

    def test_detects_left_column_hyphen(self):
        self.assertTrue(column_ends_hyphenated(self._pdf("decompi-"), 0))

    def test_plain_left_column_is_not_hyphenated(self):
        self.assertFalse(column_ends_hyphenated(self._pdf("the end"), 0))


class TestAlignOrderedListMarkers(unittest.TestCase):
    def test_restores_sequence_when_translator_repeats_one(self):
        source = "1. First\n2. Second\n3. Third"
        translated = "1. 第一\n1. 第二\n1. 第三"
        self.assertEqual(
            align_ordered_list_markers(source, translated),
            "1. 第一\n2. 第二\n3. 第三",
        )

    def test_preserves_parenthesis_style(self):
        source = "1) First\n2) Second"
        translated = "1) 第一\n1) 第二"
        self.assertEqual(
            align_ordered_list_markers(source, translated),
            "1) 第一\n2) 第二",
        )

    def test_mismatched_item_count_is_unchanged(self):
        source = "1. First\n2. Second"
        translated = "1. 合并成一段"
        self.assertEqual(align_ordered_list_markers(source, translated), translated)


class TestFigureRegions(unittest.TestCase):
    def _pdf(self, draw: bool) -> bytes:
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        if draw:
            page.draw_rect(
                pymupdf.Rect(100, 100, 400, 300),
                color=(0, 0, 0),
                fill=(0.8, 0.9, 1.0),
            )
            for y in range(130, 280, 30):
                page.draw_line((120, y), (380, y), color=(0, 0, 0))
        page.insert_text((80, 400), "Body text that is not a figure.")
        return doc.tobytes()

    def test_finds_vector_figure(self):
        regions = figure_regions(self._pdf(draw=True), 0)
        self.assertTrue(regions, "a large vector drawing should be a figure")
        self.assertGreater(regions[0][2] - regions[0][0], 200)

    def test_plain_text_page_has_no_figure(self):
        self.assertEqual(figure_regions(self._pdf(draw=False), 0), [])


if __name__ == "__main__":
    unittest.main()
