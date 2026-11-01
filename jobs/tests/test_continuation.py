"""Tests for cross-page paragraph merging."""

import unittest

from src.bilingual.continuation import (
    merge_column_paragraphs,
    merge_continuations,
    merge_ordered_list_blocks,
    merge_page_paragraphs,
)


def page(index: int, *blocks: dict) -> dict:
    return {"index": index, "blocks": list(blocks)}


def text(value: str, label: str = "text", box=None) -> dict:
    block = {"label": label, "text": value}
    if box is not None:
        block["boxes_norm"] = [box]
    return block


class TestMergePageParagraphs(unittest.TestCase):
    def test_merges_paragraph_split_by_page_break(self):
        pages = [
            page(0, text("This paragraph was cut at the bottom of the page because the author had more to say and"),
                 text("", label="image")),
            page(1, text("continues at the top of the next page and ends here.")),
        ]
        self.assertEqual(merge_page_paragraphs(pages), 1)
        self.assertIn("more to say and continues", pages[0]["blocks"][0]["text"])
        self.assertEqual(pages[1]["blocks"], [])

    def test_hyphenated_word_is_rejoined_without_space(self):
        pages = [
            page(0, text("Researchers have reformulated the task as a translation exercise, calling the process decompi")),
            page(1, text("lation as a translation exercise, which is a common framing in this field.")),
        ]
        self.assertEqual(merge_page_paragraphs(pages, hyphenated_pages={0}), 1)
        self.assertIn("decompilation as a translation", pages[0]["blocks"][0]["text"])

    def test_does_not_merge_after_terminal_punctuation(self):
        pages = [
            page(0, text("This paragraph is complete.")),
            page(1, text("another paragraph starts here with lowercase.")),
        ]
        self.assertEqual(merge_page_paragraphs(pages), 0)
        self.assertEqual(len(pages[1]["blocks"]), 1)

    def test_heading_on_next_page_blocks_merge(self):
        pages = [
            page(0, text("A paragraph that was cut without punctuation at its")),
            page(1, text("## 3.1 A New Section", label="sub_title"),
                 text("a new section starts here.")),
        ]
        self.assertEqual(merge_page_paragraphs(pages), 0)

    def test_media_between_the_fragments_does_not_block_merge(self):
        pages = [
            page(0, text("The paragraph continues past a figure at the bottom of the page and then keeps going"), {"label": "image", "text": "", "image_key": "k"}),
            page(1, {"label": "table", "text": "<table><td>1</td></table>"},
                 text("and resumes here with the rest of the paragraph.")),
        ]
        self.assertEqual(merge_page_paragraphs(pages), 1)
        self.assertIn("keeps going and resumes", pages[0]["blocks"][0]["text"])

    def test_list_item_is_not_a_continuation(self):
        pages = [
            page(0, text("The paragraph ends without punctuation but the next block is")),
            page(1, text("- a list item that should stay on its page.")),
        ]
        self.assertEqual(merge_page_paragraphs(pages), 0)


class TestMergeColumnParagraphs(unittest.TestCase):
    def test_merges_left_column_into_right_column(self):
        pages = [
            page(
                0,
                text("The paragraph reaches the bottom of the left column and the author still has more to say and", box=[0.08, 0.2, 0.45, 0.8]),
                text("continues at the top of the right column with lowercase text.", box=[0.55, 0.1, 0.92, 0.3]),
            )
        ]
        self.assertEqual(merge_column_paragraphs(pages), 1)
        self.assertIn("and continues at the top", pages[0]["blocks"][0]["text"])
        self.assertEqual(len(pages[0]["blocks"]), 1)

    def test_hyphenated_column_break_has_no_space(self):
        pages = [
            page(
                0,
                text("Researchers have reformulated the task and they call the whole translation process decompi", box=[0.08, 0.2, 0.45, 0.8]),
                text("lation as a translation exercise in this field.", box=[0.55, 0.1, 0.92, 0.3]),
            )
        ]
        self.assertEqual(merge_column_paragraphs(pages, hyphenated_pages={0}), 1)
        self.assertIn("decompilation as a translation", pages[0]["blocks"][0]["text"])

    def test_terminal_punctuation_blocks_column_merge(self):
        pages = [
            page(
                0,
                text("This left column paragraph is complete.", box=[0.08, 0.2, 0.45, 0.8]),
                text("a new right column paragraph starts here.", box=[0.55, 0.1, 0.92, 0.3]),
            )
        ]
        self.assertEqual(merge_column_paragraphs(pages), 0)

    def test_right_column_heading_blocks_merge(self):
        pages = [
            page(
                0,
                text("The left column paragraph is cut without punctuation at its", box=[0.08, 0.2, 0.45, 0.8]),
                text("2.1 A New Section", label="sub_title", box=[0.55, 0.05, 0.9, 0.1]),
                text("a new paragraph starts here.", box=[0.55, 0.12, 0.92, 0.3]),
            )
        ]
        self.assertEqual(merge_column_paragraphs(pages), 0)

    def test_media_between_columns_does_not_block_merge(self):
        pages = [
            page(
                0,
                text("The paragraph is cut at the left column bottom and the author keeps writing more text and", box=[0.08, 0.2, 0.45, 0.7]),
                {"label": "image", "text": "", "image_key": "k", "boxes_norm": [[0.1, 0.72, 0.45, 0.8]]},
                text("resumes after a figure at the top of the right column.", box=[0.55, 0.1, 0.92, 0.3]),
            )
        ]
        self.assertEqual(merge_column_paragraphs(pages), 1)

    def test_one_column_page_is_not_merged(self):
        pages = [
            page(
                0,
                text("This is a full-width left block that ends without punctuation", box=[0.1, 0.2, 0.9, 0.3]),
                text("and another full-width block starts here.", box=[0.1, 0.32, 0.9, 0.4]),
            )
        ]
        self.assertEqual(merge_column_paragraphs(pages), 0)

    def test_missing_bbox_is_not_merged(self):
        pages = [
            page(
                0,
                text("A left paragraph ending without punctuation and more words"),
                text("a right paragraph that looks like a continuation."),
            )
        ]
        self.assertEqual(merge_column_paragraphs(pages), 0)

    def test_column_merge_then_page_merge(self):
        pages = [
            page(
                0,
                text("The left column paragraph reaches the bottom and the author continues writing and", box=[0.08, 0.2, 0.45, 0.8]),
                text("continues in the right column without punctuation, then", box=[0.55, 0.1, 0.92, 0.9]),
            ),
            page(1, text("finishes on the next page with lowercase text.", box=[0.08, 0.1, 0.45, 0.3])),
        ]
        self.assertEqual(merge_column_paragraphs(pages), 1)
        self.assertEqual(merge_page_paragraphs(pages), 1)
        self.assertIn("then finishes on the next page", pages[0]["blocks"][0]["text"])


class TestMergeOrderedLists(unittest.TestCase):
    def test_joins_consecutive_numbered_items(self):
        pages = [
            page(
                0,
                text("1. First item"),
                text("2. Second item"),
                text("3. Third item"),
            )
        ]
        self.assertEqual(merge_ordered_list_blocks(pages), 2)
        self.assertEqual(len(pages[0]["blocks"]), 1)
        self.assertEqual(
            pages[0]["blocks"][0]["text"],
            "1. First item\n2. Second item\n3. Third item",
        )

    def test_joins_across_page_boundary(self):
        pages = [
            page(0, text("1. First item")),
            page(1, text("2. Second item")),
        ]
        self.assertEqual(merge_ordered_list_blocks(pages), 1)
        self.assertIn("2. Second item", pages[0]["blocks"][0]["text"])
        self.assertEqual(pages[1]["blocks"], [])

    def test_restarted_list_stays_separate(self):
        pages = [
            page(0, text("1. First item"), text("2. Second item")),
            page(1, text("1. Another list"), text("2. Another item")),
        ]
        merge_ordered_list_blocks(pages)
        self.assertEqual(len(pages[0]["blocks"]), 1)
        self.assertEqual(len(pages[1]["blocks"]), 1)
        self.assertTrue(pages[1]["blocks"][0]["text"].startswith("1."))

    def test_heading_is_not_a_list_item(self):
        pages = [
            page(
                0,
                text("1. Introduction", label="sub_title"),
                text("2. Related Work", label="sub_title"),
            )
        ]
        self.assertEqual(merge_ordered_list_blocks(pages), 0)

    def test_non_consecutive_numbers_are_not_merged(self):
        pages = [page(0, text("1. First item"), text("3. Skipped one"))]
        self.assertEqual(merge_ordered_list_blocks(pages), 0)

    def test_parenthesis_marker_is_preserved(self):
        pages = [page(0, text("1) First item"), text("2) Second item"))]
        self.assertEqual(merge_ordered_list_blocks(pages), 1)
        self.assertIn("2) Second item", pages[0]["blocks"][0]["text"])


class TestMergeContinuations(unittest.TestCase):
    def test_page_opening_after_table_is_merged(self):
        pages = [
            page(0, text("The previous page paragraph is cut before its final words and", box=[0.1, 0.2, 0.9, 0.4])),
            page(1, {"label": "table", "text": "<table><td>x</td></table>", "boxes_norm": [[0.1, 0.1, 0.9, 0.3]]},
                 text("continues after a table at the top of the next page.", box=[0.1, 0.35, 0.9, 0.5])),
        ]
        self.assertEqual(merge_continuations(pages), 1)
        self.assertIn("final words and continues after a table", pages[0]["blocks"][0]["text"])
        self.assertEqual(len(pages[1]["blocks"]), 1)

    def test_first_column_images_then_right_column_continuation(self):
        pages = [
            page(0, text("The paragraph ends at the bottom of the previous page and", box=[0.1, 0.7, 0.45, 0.9])),
            page(
                1,
                {"label": "image", "text": "", "image_key": "k", "boxes_norm": [[0.1, 0.1, 0.45, 0.8]]},
                text("continues at the top of the right column after images.", box=[0.55, 0.1, 0.9, 0.3]),
            ),
        ]
        self.assertEqual(merge_continuations(pages), 1)
        self.assertIn("previous page and continues at the top", pages[0]["blocks"][0]["text"])
        self.assertEqual(len(pages[1]["blocks"]), 1)
        self.assertEqual(pages[1]["blocks"][0]["label"], "image")

    def test_page_opening_uppercase_is_not_merged(self):
        pages = [
            page(0, text("A paragraph ends without punctuation and", box=[0.1, 0.2, 0.9, 0.4])),
            page(1, text("A brand new paragraph starts with a capital letter.", box=[0.1, 0.1, 0.9, 0.3])),
        ]
        self.assertEqual(merge_continuations(pages), 0)

    def test_page_opening_after_heading_is_not_merged(self):
        pages = [
            page(0, text("A paragraph ends without punctuation and", box=[0.1, 0.2, 0.9, 0.4])),
            page(1, text("2. A New Section", label="sub_title", box=[0.1, 0.1, 0.9, 0.15]),
                 text("a new section paragraph starts here.", box=[0.1, 0.18, 0.9, 0.3])),
        ]
        self.assertEqual(merge_continuations(pages), 0)

    def test_list_between_blocks_blocks_merge(self):
        pages = [
            page(0, text("A paragraph ends without punctuation and", box=[0.1, 0.2, 0.9, 0.4])),
            page(1, text("1. A list item first", box=[0.1, 0.1, 0.9, 0.2]),
                 text("continues with lowercase text after a list.", box=[0.1, 0.25, 0.9, 0.4])),
        ]
        self.assertEqual(merge_continuations(pages), 0)

    def test_hyphenated_page_opening_has_no_space(self):
        pages = [
            page(0, text("Researchers reformulated the task and call the process decompi", box=[0.1, 0.2, 0.45, 0.8])),
            page(1, text("lation as a translation exercise on the next page.", box=[0.1, 0.1, 0.45, 0.3])),
        ]
        self.assertEqual(
            merge_continuations(pages, hyphenated_blocks={id(pages[0]["blocks"][0])}),
            1,
        )
        self.assertIn("decompilation as a translation", pages[0]["blocks"][0]["text"])

    def test_right_column_lowercase_after_left_body_is_merged(self):
        pages = [
            page(
                0,
                text("The left column ends mid-sentence and the author", box=[0.1, 0.2, 0.45, 0.8]),
                text("continues in the right column with lowercase text.", box=[0.55, 0.1, 0.9, 0.3]),
            )
        ]
        self.assertEqual(merge_continuations(pages), 1)
        self.assertIn("author continues in the right column", pages[0]["blocks"][0]["text"])

    def test_bullet_list_between_blocks_blocks_merge(self):
        pages = [
            page(0, text("A paragraph ends without punctuation and", box=[0.1, 0.2, 0.9, 0.4])),
            page(1, text("- a bullet item stays where it is", box=[0.1, 0.1, 0.9, 0.2]),
                 text("continues with lowercase text after a bullet.", box=[0.1, 0.25, 0.9, 0.4])),
        ]
        self.assertEqual(merge_continuations(pages), 0)

    def test_opening_quote_before_lowercase_is_a_continuation(self):
        pages = [
            page(0, text("A paragraph ends without punctuation and", box=[0.1, 0.2, 0.9, 0.4])),
            page(1, text("“lowercase quoted continuation on the next page.", box=[0.1, 0.1, 0.9, 0.3])),
        ]
        self.assertEqual(merge_continuations(pages), 1)
        self.assertIn("and “lowercase quoted", pages[0]["blocks"][0]["text"])

    def test_long_numbered_paragraph_can_continue(self):
        pages = [
            page(
                0,
                text(
                    "1) CFG Extraction: We start by extracting the CFG from the binary code, "
                    "which is widely used in binary analysis, and the extraction finishes with",
                    box=[0.08, 0.2, 0.45, 0.8],
                ),
            ),
            page(1, text("normalize the instructions within the CFG basic blocks.", box=[0.1, 0.1, 0.45, 0.3])),
        ]
        self.assertEqual(merge_continuations(pages), 1)
        self.assertIn("finishes with normalize", pages[0]["blocks"][0]["text"])

    def test_right_column_after_page_heading_merges_same_page(self):
        pages = [
            page(
                0,
                text("## 4 A Section", label="heading", box=[0.1, 0.05, 0.9, 0.1]),
                text("The left column paragraph starts here and continues with", box=[0.1, 0.2, 0.45, 0.8]),
                text("the right column continuation of the same paragraph.", box=[0.55, 0.1, 0.9, 0.3]),
            )
        ]
        self.assertEqual(merge_continuations(pages), 1)
        self.assertIn("continues with the right column", pages[0]["blocks"][1]["text"])

    def test_right_column_after_heading_does_not_cross_from_previous_page(self):
        pages = [
            page(0, text("A previous page paragraph is cut without punctuation and", box=[0.1, 0.2, 0.9, 0.4])),
            page(
                1,
                text("## 5 A New Section", label="heading", box=[0.1, 0.05, 0.9, 0.1]),
                {"label": "image", "text": "", "image_key": "k", "boxes_norm": [[0.1, 0.2, 0.45, 0.8]]},
                text("a right column new paragraph starts here.", box=[0.55, 0.1, 0.9, 0.3]),
            ),
        ]
        self.assertEqual(merge_continuations(pages), 0)

    def test_completed_previous_sentence_blocks_merge(self):
        pages = [
            page(0, text("This paragraph is complete.", box=[0.1, 0.2, 0.9, 0.4])),
            page(1, text("lowercase text after a completed sentence.", box=[0.1, 0.1, 0.9, 0.3])),
        ]
        self.assertEqual(merge_continuations(pages), 0)


if __name__ == "__main__":
    unittest.main()
