"""Tests for Markdown/ZIP/DOCX export of the final OCR reading result."""

import base64
import io
import re
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from app.helpers.paper_export import (
    ExportError,
    build_markdown,
    build_zip,
    content_disposition,
    convert_docx,
    docx_cache_key,
)

# 1x1 red PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z2ioAAAAASUVORK5CYII="
)


def make_document(lang_images=True):
    blocks = [
        {
            "label": "heading",
            "zh_md": "# 中文标题",
            "en_md": "# English title",
            "boxes_norm": [],
        },
        {
            "label": "text",
            "zh_md": "普通正文。",
            "en_md": "Ordinary body.",
            "boxes_norm": [],
        },
        {
            "label": "table",
            "zh_md": "",
            "en_md": "",
            "image_key": "papers/p/ocr/d/assets/table.png",
            "boxes_norm": [],
        },
        {
            "label": "caption",
            "zh_md": "表 1：测试表格。",
            "en_md": "Table 1: Test table.",
            "boxes_norm": [],
        },
        {
            "label": "equation",
            "zh_md": "$$\nE = mc^2\n$$",
            "en_md": "$$\nE = mc^2\n$$",
            "boxes_norm": [],
        },
    ]
    if lang_images:
        blocks.append(
            {
                "label": "figure",
                "zh_md": "",
                "en_md": "",
                "image_key": "papers/p/ocr/d/assets/figure.png",
                "boxes_norm": [],
            }
        )
    return SimpleNamespace(
        id="doc-id",
        paper_id="paper-id",
        pages=[{"index": 0, "blocks": blocks}],
    )


class TestMarkdownExport(unittest.TestCase):
    def test_uses_final_blocks_and_relative_assets(self):
        markdown, assets = build_markdown(make_document(), lang="zh")
        self.assertIn("# 中文标题", markdown)
        self.assertIn("普通正文。", markdown)
        self.assertIn("![Table](assets/table.png)", markdown)
        self.assertIn("![Figure](assets/figure.png)", markdown)
        self.assertIn("表 1：测试表格。", markdown)
        self.assertIn("$$\nE = mc^2\n$$", markdown)
        self.assertNotIn("<table", markdown.lower())
        self.assertEqual(
            assets,
            {
                "table.png": "papers/p/ocr/d/assets/table.png",
                "figure.png": "papers/p/ocr/d/assets/figure.png",
            },
        )

    def test_english_export_uses_en_md(self):
        markdown, _ = build_markdown(make_document(), lang="en")
        self.assertIn("# English title", markdown)
        self.assertIn("Ordinary body.", markdown)
        self.assertNotIn("中文标题", markdown)

    def test_docx_image_attributes(self):
        markdown, _ = build_markdown(
            make_document(), lang="zh", image_attrs=True
        )
        self.assertIn("{width=6.5in}", markdown)

    def test_untrusted_legacy_url_is_skipped(self):
        document = make_document(lang_images=False)
        document.pages[0]["blocks"][2].pop("image_key", None)
        document.pages[0]["blocks"].append(
            {
                "label": "figure",
                "zh_md": "",
                "en_md": "",
                "image_url": "https://evil.example.com/steal.png",
                "boxes_norm": [],
            }
        )
        markdown, assets = build_markdown(document, lang="zh")
        self.assertNotIn("evil.example.com", markdown)
        self.assertEqual(assets, {})

    def test_no_images(self):
        document = make_document(lang_images=False)
        document.pages[0]["blocks"][2].pop("image_key", None)
        markdown, assets = build_markdown(document, lang="en")
        self.assertIn("English title", markdown)
        self.assertEqual(assets, {})


class TestZipExport(unittest.TestCase):
    def test_zip_contains_content_and_matching_assets(self):
        markdown, assets = build_markdown(make_document(), lang="zh")
        data = build_zip(
            markdown,
            {
                "table.png": PNG,
                "figure.png": PNG,
            },
        )
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = archive.namelist()
        self.assertIn("content.md", names)
        self.assertIn("assets/table.png", names)
        self.assertIn("assets/figure.png", names)
        links = re.findall(r"\]\((assets/[^)]+)\)", archive.read("content.md").decode())
        self.assertTrue(links)
        for link in links:
            self.assertIn(link, names)


class TestDocxExport(unittest.TestCase):
    def test_docx_has_images_and_math(self):
        markdown, _ = build_markdown(make_document(), lang="zh", image_attrs=True)
        data = convert_docx(
            markdown,
            {"table.png": PNG, "figure.png": PNG},
        )
        self.assertTrue(data.startswith(b"PK"))
        archive = zipfile.ZipFile(io.BytesIO(data))
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        self.assertEqual(len(media), 2)
        xml = archive.read("word/document.xml").decode("utf-8", "replace")
        self.assertIn("<m:oMath", xml)
        self.assertNotIn('r:link="http', xml)
        self.assertNotIn("{width=6.5in}", xml)

    def test_docx_without_images(self):
        document = make_document(lang_images=False)
        document.pages[0]["blocks"][2].pop("image_key", None)
        markdown, _ = build_markdown(document, lang="en")
        data = convert_docx(markdown, {})
        archive = zipfile.ZipFile(io.BytesIO(data))
        self.assertTrue(archive.namelist())


class TestCacheAndHeaders(unittest.TestCase):
    def test_cache_key_changes_with_content_and_language(self):
        doc = make_document()
        markdown, assets = build_markdown(doc, lang="zh")
        first = docx_cache_key(doc, "zh", markdown, assets)
        self.assertNotEqual(first, docx_cache_key(doc, "en", markdown, assets))
        self.assertNotEqual(
            first, docx_cache_key(doc, "zh", markdown + "\nextra", assets)
        )
        other_assets = dict(assets)
        other_assets["table.png"] = "papers/p/ocr/d/assets/table2.png"
        self.assertNotEqual(first, docx_cache_key(doc, "zh", markdown, other_assets))
        doc.updated_at = "2026-11-01T00:00:01"
        self.assertNotEqual(first, docx_cache_key(doc, "zh", markdown, assets))

    def test_chinese_content_disposition(self):
        header = content_disposition("中文 论文", ".docx")
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("%E4%B8%AD%E6%96%87", header)
        self.assertIn(".docx", header)

    def test_missing_image_is_explicit(self):
        document = make_document(lang_images=False)
        document.pages[0]["blocks"].append(
            {
                "label": "table",
                "zh_md": "",
                "en_md": "",
                "image_key": "papers/p/ocr/d/assets/missing.png",
                "boxes_norm": [],
            }
        )
        with patch(
            "app.helpers.paper_export.s3_service.download_bytes",
            side_effect=Exception("not found"),
        ):
            from app.helpers.paper_export import collect_assets

            with self.assertRaises(ExportError):
                collect_assets({"missing.png": "papers/p/ocr/d/assets/missing.png"})


if __name__ == "__main__":
    unittest.main()
