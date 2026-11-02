"""Regression tests for the bilingual helpers that run inside the worker.

Run from the jobs directory:

    uv run python -m unittest discover -s tests
"""

import unittest

from src.bilingual.text_normalise import align_ordered_list_markers, normalise_math


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
