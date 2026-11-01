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


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _styles(docx_bytes):
    import xml.etree.ElementTree as ET

    archive = zipfile.ZipFile(io.BytesIO(docx_bytes))
    return ET.fromstring(archive.read("word/styles.xml"))


def _style_map(styles):
    return {style.get(W + "styleId"): style for style in styles.findall(W + "style")}


def _document(docx_bytes):
    import xml.etree.ElementTree as ET

    archive = zipfile.ZipFile(io.BytesIO(docx_bytes))
    return ET.fromstring(archive.read("word/document.xml"))


def _paragraph_style(paragraph):
    ppr = paragraph.find(W + "pPr")
    if ppr is None:
        return None
    style = ppr.find(W + "pStyle")
    return style.get(W + "val") if style is not None else None


def _style_by_name(styles, name):
    for style in styles.findall(W + "style"):
        node = style.find(W + "name")
        if node is not None and (node.get(W + "val") or "").lower() == name.lower():
            return style
    raise AssertionError(f"style not found: {name}")


def _style_chain(styles, style):
    by_id = {item.get(W + "styleId"): item for item in styles.findall(W + "style")}
    current = style
    while current is not None:
        yield current
        based = current.find(W + "basedOn")
        current = by_id.get(based.get(W + "val")) if based is not None else None


def _effective(styles, style, section, tag):
    for item in _style_chain(styles, style):
        parent = item.find(W + section)
        if parent is not None:
            node = parent.find(W + tag)
            if node is not None:
                return node
    defaults = styles.find(W + "docDefaults")
    if defaults is not None:
        default_section = defaults.find(W + ("rPrDefault" if section == "rPr" else "pPrDefault"))
        if default_section is not None:
            parent = default_section.find(W + section)
            if parent is not None:
                return parent.find(W + tag)
    return None


def _style_names(docx_bytes):
    return {
        item.get(W + "styleId"): (
            item.find(W + "name").get(W + "val")
            if item.find(W + "name") is not None
            else ""
        )
        for item in _styles(docx_bytes).findall(W + "style")
    }


class TestReferenceDocStyles(unittest.TestCase):
    def test_reference_template_is_present(self):
        from app.helpers.paper_export import REFERENCE_DOC

        self.assertTrue(REFERENCE_DOC.endswith("academic-reference.docx"))
        self.assertTrue(__import__("os").path.exists(REFERENCE_DOC))

    def test_body_fonts_size_spacing_and_first_line(self):
        from app.helpers.paper_export import REFERENCE_DOC

        with open(REFERENCE_DOC, "rb") as handle:
            styles = _styles(handle.read())
        normal = _style_by_name(styles, "Normal")
        body_text = _style_by_name(styles, "Body Text")
        first = _style_by_name(styles, "First Paragraph")

        fonts = _effective(styles, normal, "rPr", "rFonts")
        self.assertEqual(fonts.get(W + "ascii"), "Times New Roman")
        self.assertEqual(fonts.get(W + "hAnsi"), "Times New Roman")
        self.assertEqual(fonts.get(W + "eastAsia"), "宋体")
        for style in (normal, body_text, first):
            size = _effective(styles, style, "rPr", "sz")
            self.assertEqual(size.get(W + "val"), "24")
        for style in (body_text, first):
            ind = _effective(styles, style, "pPr", "ind")
            self.assertEqual(ind.get(W + "firstLineChars"), "200")
            self.assertEqual(ind.get(W + "firstLine"), "480")
            spacing = _effective(styles, style, "pPr", "spacing")
            self.assertEqual(spacing.get(W + "before") or "0", "0")
            self.assertEqual(spacing.get(W + "after") or "0", "0")
            # No w:line means Word's single-spacing default.
            self.assertIn(spacing.get(W + "line"), (None, "240"))

    def test_heading_sizes_and_bold(self):
        from app.helpers.paper_export import REFERENCE_DOC

        with open(REFERENCE_DOC, "rb") as handle:
            styles = _styles(handle.read())
        expected = {
            "heading 1": "32",
            "heading 2": "28",
            "heading 3": "24",
            "heading 4": "22",
            "heading 5": "21",
            "heading 6": "20",
        }
        for name, size in expected.items():
            style = _style_by_name(styles, name)
            actual_size = _effective(styles, style, "rPr", "sz")
            self.assertEqual(actual_size.get(W + "val"), size)
            self.assertIsNotNone(_effective(styles, style, "rPr", "b"))
            ind = _effective(styles, style, "pPr", "ind")
            if ind is not None:
                self.assertEqual(ind.get(W + "firstLineChars"), "0")
            spacing = _effective(styles, style, "pPr", "spacing")
            self.assertIsNotNone(spacing.get(W + "before"))
            self.assertIsNotNone(spacing.get(W + "after"))

    def test_generated_docx_uses_figure_and_caption_styles(self):
        markdown, _ = build_markdown(make_document(), lang="zh", image_attrs=True)
        data = convert_docx(markdown, {"table.png": PNG, "figure.png": PNG})
        doc = _document(data)
        caption_style = None
        image_styles = []
        for paragraph in doc.iter(W + "p"):
            if list(paragraph.iter(W + "drawing")):
                image_styles.append(_paragraph_style(paragraph))
            text = "".join(t.text or "" for t in paragraph.iter(W + "t"))
            if "表 1" in text:
                caption_style = _paragraph_style(paragraph)
        self.assertTrue(image_styles)
        self.assertTrue(all(style == "Figure" for style in image_styles))
        self.assertEqual(caption_style, "TableCaption")

        for paragraph in doc.iter(W + "p"):
            if _paragraph_style(paragraph) == "Figure":
                ppr = paragraph.find(W + "pPr")
                self.assertIsNone(ppr.find(W + "ind"))

        body_seen = False
        for paragraph in doc.iter(W + "p"):
            if _paragraph_style(paragraph) in {"BodyText", "FirstParagraph"}:
                body_seen = True
                ppr = paragraph.find(W + "pPr")
                self.assertIsNone(ppr.find(W + "ind"))
        self.assertTrue(body_seen)

    def test_figure_caption_uses_image_caption(self):
        document = make_document()
        document.pages[0]["blocks"].append(
            {
                "label": "caption",
                "zh_md": "图 1：测试图。",
                "en_md": "Figure 1: Test figure.",
                "boxes_norm": [],
            }
        )
        markdown, _ = build_markdown(document, lang="zh", image_attrs=True)
        data = convert_docx(markdown, {"table.png": PNG, "figure.png": PNG})
        doc = _document(data)
        found = None
        for paragraph in doc.iter(W + "p"):
            text = "".join(t.text or "" for t in paragraph.iter(W + "t"))
            if "图 1" in text:
                found = _paragraph_style(paragraph)
        self.assertEqual(found, "ImageCaption")

    def test_generated_heading_levels(self):
        markdown = "\n\n".join(
            f"{'#' * level} H{level}" for level in range(1, 7)
        ) + "\n\n正文。\n"
        data = convert_docx(markdown, {})
        doc = _document(data)
        names = _style_names(data)
        found = {}
        for paragraph in doc.iter(W + "p"):
            text = "".join(t.text or "" for t in paragraph.iter(W + "t")).strip()
            if text.startswith("H") and text[1:].isdigit():
                found[text] = names.get(_paragraph_style(paragraph), "")
        for level in range(1, 7):
            self.assertEqual(found.get(f"H{level}"), f"heading {level}")
