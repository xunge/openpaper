"""MinerU client, safety and structured Markdown tests.

No network is used: the client takes an injectable session, so the 200/202/tasks
protocols and their failure modes are exercised deterministically. The layout
tests build a small PDF with a table, pseudocode, code and a figure, then assert
that special regions become images and never leak HTML/code fences into the
reader Markdown.
"""

import io
import json
import unittest
import zipfile
from unittest.mock import patch

import pymupdf
from PIL import Image

from src.bilingual import mineru_layout
from src.bilingual.mineru_client import (
    MineruClient,
    MineruConfig,
    MineruError,
    MineruUnavailable,
    MineruZip,
    _rewrite_loopback,
)
from src.bilingual.mineru_layout import build_pages, crop_pdf_region


# --------------------------------------------------------------------------- helpers
class FakeResponse:
    def __init__(self, status=200, *, json_data=None, content=b"", headers=None):
        self.status_code = status
        self._json = json_data
        self.content = content
        self.headers = headers or {}
        self.text = content.decode("utf-8", "replace") if content else ""

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024 * 1024):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, *, post=None, get=None, put=None):
        self._post = post
        self._get = get
        self._put = put
        self.calls = []

    def mount(self, prefix, adapter):
        pass

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._post(url, kwargs)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._get(url, kwargs)

    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        return self._put(url, kwargs)


def make_zip(markdown: str = "# T\n", middle=None, structured=None, assets=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("markdown.md", markdown)
        archive.writestr("middle_json.json", json.dumps(middle or {}))
        archive.writestr("structured_content.json", json.dumps(structured or {}))
        for name, data in (assets or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def png_bytes(color=(255, 0, 0)) -> bytes:
    image = Image.new("RGB", (20, 20), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def table_structured(continue_prev=False, bbox=None):
    return {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "table",
                        "bbox": bbox if bbox is not None else [0.1, 0.2, 0.5, 0.4],
                        "continue_prev": continue_prev,
                        "continues_prev": continue_prev,
                        "content": "<table><tr><td>1</td></tr></table>",
                        "captions": [
                            {
                                "bbox": [0.1, 0.41, 0.5, 0.45],
                                "content": "Table 1: A table.",
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_pdf() -> bytes:
    """A page with a table, pseudocode, code and a figure at known positions."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "A Test Paper", fontsize=18)
    page.insert_text((60, 110), "Ordinary body paragraph.", fontsize=11)
    # table
    page.draw_rect(pymupdf.Rect(60, 150, 300, 250), color=(0, 0, 0))
    page.draw_line((60, 180), (300, 180), color=(0, 0, 0))
    page.insert_text((70, 175), "Col A    Col B", fontsize=10)
    page.insert_text((70, 205), "1        2", fontsize=10)
    page.insert_text((60, 280), "Table 1: A table.", fontsize=9)
    # algorithm
    page.insert_text((60, 320), "Algorithm 1: Solve", fontsize=11)
    page.insert_text((70, 340), "Require: n", fontsize=9)
    page.insert_text((70, 355), "Ensure: answer", fontsize=9)
    page.insert_text((70, 370), "return n", fontsize=9)
    # code
    page.insert_text((60, 420), "Listing 1: example", fontsize=10)
    page.insert_text((70, 440), "def solve(n):", fontsize=9, fontname="cour")
    page.insert_text((70, 455), "    return n + 1", fontsize=9, fontname="cour")
    # figure
    page.draw_rect(pymupdf.Rect(60, 500, 300, 650), color=(0, 0, 1), fill=(0.8, 0.8, 1))
    page.insert_text((60, 680), "Figure 1: A figure.", fontsize=9)
    # equation text
    page.insert_text((60, 720), "E = mc^2", fontsize=11)
    return doc.tobytes()


def builder_structured():
    return {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {"type": "doc_title", "bbox": [0.1, 0.04, 0.6, 0.08], "level": 1, "content": "A Test Paper"},
                    {"type": "text", "bbox": [0.1, 0.1, 0.6, 0.14], "content": "Ordinary body paragraph."},
                    {
                        "type": "table",
                        "bbox": [0.1, 0.16, 0.52, 0.31],
                        "content": "<table><tr><td>1</td><td>2</td></tr></table>",
                        "captions": [{"bbox": [0.1, 0.32, 0.5, 0.35], "content": "Table 1: A table."}],
                    },
                    {
                        "type": "code",
                        "bbox": [0.1, 0.36, 0.55, 0.45],
                        "content": "Algorithm 1: Solve\nRequire: n\nEnsure: answer\nreturn n",
                        "captions": [{"bbox": [0.1, 0.46, 0.5, 0.49], "content": "Algorithm 1: Solve"}],
                    },
                    {
                        "type": "code",
                        "bbox": [0.1, 0.5, 0.55, 0.6],
                        "content": "```python\ndef solve(n):\n    return n + 1\n```",
                        "captions": [],
                    },
                    {
                        "type": "image",
                        "bbox": [0.1, 0.62, 0.55, 0.8],
                        "content": "",
                        "captions": [{"bbox": [0.1, 0.81, 0.5, 0.84], "content": "Figure 1: A figure."}],
                        "image_source": "images/page_1_image_1.png",
                    },
                    {"type": "equation", "bbox": [0.1, 0.86, 0.5, 0.9], "content": "E = mc^2"},
                ],
            }
        ]
    }


def builder_middle():
    return {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {"type": "doc_title"},
                    {"type": "text"},
                    {"type": "table"},
                    {"type": "code", "sub_type": "code", "guess_lang": None},
                    {"type": "code", "sub_type": "code", "guess_lang": "python"},
                    {"type": "image"},
                    {"type": "equation"},
                ],
            }
        ]
    }


# --------------------------------------------------------------------------- client
class TestMineruClient(unittest.TestCase):
    def config(self, **kwargs):
        base = dict(
            file_parse_url="http://mineru.test:8003",
            tasks_api_url="http://mineru.test:8000",
            request_timeout=10,
            task_timeout=10,
            poll_interval=0,
            file_wait_timeout=1,
        )
        base.update(kwargs)
        return MineruConfig(**base)

    def test_file_parse_200_returns_zip(self):
        zip_data = make_zip()
        session = FakeSession(
            post=lambda url, kw: FakeResponse(
                200, content=zip_data, headers={"content-type": "application/zip"}
            )
        )
        client = MineruClient(self.config(), session=session)
        bundle = client.parse(b"pdf", "paper.pdf")
        self.assertIn("markdown.md", bundle.names)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][0], "POST")

    def test_file_parse_202_polls_result(self):
        zip_data = make_zip()
        statuses = [
            FakeResponse(200, json_data={"status": "running"}),
            FakeResponse(
                200,
                json_data={
                    "status": "completed",
                    "result_url": "http://mineru.test:8003/v1/file_parse/abc/result",
                },
            ),
        ]
        session = FakeSession(
            post=lambda url, kw: FakeResponse(
                202,
                json_data={
                    "status": "running",
                    "status_url": "http://mineru.test:8003/v1/file_parse/abc",
                },
            ),
            get=lambda url, kw: statuses.pop(0) if statuses else FakeResponse(200, content=zip_data, headers={"content-type": "application/zip"}),
        )
        client = MineruClient(self.config(), session=session)
        bundle = client.parse(b"pdf", "paper.pdf")
        self.assertIn("structured_content.json", bundle.names)
        self.assertEqual(session.calls[0][0], "POST")
        self.assertGreaterEqual(sum(1 for call in session.calls if call[0] == "GET"), 2)

    def test_file_parse_timeout_recovers_same_request_id(self):
        zip_data = make_zip()
        import requests

        def post(url, kw):
            raise requests.ReadTimeout("slow")

        calls = {"get": 0}

        def get(url, kw):
            calls["get"] += 1
            if calls["get"] == 1:
                return FakeResponse(200, json_data={"status": "running"})
            return FakeResponse(
                200,
                json_data={
                    "status": "completed",
                    "result_url": "http://mineru.test:8003/v1/file_parse/abc/result",
                },
            )

        session = FakeSession(post=post, get=get)
        client = MineruClient(self.config(), session=session)
        # The recovery path downloads from result_url; make the third GET the ZIP.
        original_get = session.get

        def get_with_zip(url, **kwargs):
            if url.endswith("/result"):
                return FakeResponse(200, content=zip_data, headers={"content-type": "application/zip"})
            return original_get(url, **kwargs)

        session.get = get_with_zip
        bundle = client.parse(b"pdf", "paper.pdf")
        self.assertIn("markdown.md", bundle.names)
        self.assertEqual(sum(1 for call in session.calls if call[0] == "POST"), 1)

    def test_file_parse_error(self):
        session = FakeSession(post=lambda url, kw: FakeResponse(500, content=b"boom"))
        client = MineruClient(self.config(), session=session)
        with self.assertRaises(MineruError):
            client.parse(b"pdf", "paper.pdf")

    def test_tasks_api_upload_poll_and_download(self):
        zip_data = make_zip()
        stage = {"created": False}

        def post(url, kw):
            if url.endswith("/v1/tasks"):
                return FakeResponse(
                    202,
                    json_data={
                        "task_id": "task1",
                        "status": "waiting-file",
                        "files": [
                            {
                                "file_id": "f1",
                                "file_name": "paper.pdf",
                                "status": "waiting-file",
                                "upload": {
                                    "url": "http://mineru.test:8000/upload/f1",
                                    "headers": {"X-Test": "1"},
                                },
                            }
                        ],
                    },
                )
            raise AssertionError(url)

        def put(url, kw):
            return FakeResponse(200, json_data={"task_id": "task1", "file_id": "f1", "status": "pending"})

        def get(url, kw):
            if url.endswith("/v1/tasks/task1"):
                return FakeResponse(
                    200,
                    json_data={
                        "task_id": "task1",
                        "status": "completed",
                        "files": [
                            {
                                "file_id": "f1",
                                "file_name": "paper.pdf",
                                "status": "completed",
                                "result_url": "http://mineru.test:8000/result/f1",
                            }
                        ],
                    },
                )
            return FakeResponse(200, content=zip_data, headers={"content-type": "application/zip"})

        session = FakeSession(post=post, put=put, get=get)
        client = MineruClient(self.config(api_mode="tasks"), session=session)
        bundle = client.parse(b"pdf", "paper.pdf")
        self.assertIn("markdown.md", bundle.names)
        self.assertTrue(any(call[0] == "PUT" for call in session.calls))

    def test_auto_falls_back_to_tasks_api(self):
        client = MineruClient(self.config(), session=FakeSession())
        bundle = MineruZip(make_zip(), MineruConfig())
        with patch.object(client, "_parse_file_parse", side_effect=MineruUnavailable("off")):
            with patch.object(client, "_parse_tasks", return_value=bundle) as tasks:
                result = client.parse(b"pdf", "paper.pdf")
        self.assertIs(result, bundle)
        tasks.assert_called_once()

    def test_loopback_links_are_rewritten_for_containers(self):
        self.assertEqual(
            _rewrite_loopback(
                "http://127.0.0.1:8003/v1/file_parse/x",
                "http://host.docker.internal:8003",
            ),
            "http://host.docker.internal:8003/v1/file_parse/x",
        )
        external = "https://cdn.example.com/result.zip"
        self.assertEqual(
            _rewrite_loopback(external, "http://host.docker.internal:8003"), external
        )

    def test_pdf_size_limit(self):
        client = MineruClient(self.config(max_pdf_bytes=10), session=FakeSession())
        with self.assertRaises(MineruError):
            client.parse(b"x" * 11, "paper.pdf")


class TestMineruZipSafety(unittest.TestCase):
    def test_rejects_traversal(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../evil.txt", "x")
        with self.assertRaises(MineruError):
            MineruZip(buffer.getvalue(), MineruConfig())

    def test_rejects_absolute_path(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("/etc/passwd", "x")
        with self.assertRaises(MineruError):
            MineruZip(buffer.getvalue(), MineruConfig())

    def test_rejects_oversized_uncompressed(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("markdown.md", "x" * 100)
        with self.assertRaises(MineruError):
            MineruZip(buffer.getvalue(), MineruConfig(max_uncompressed_bytes=10))


# --------------------------------------------------------------------------- layout
class TestBuilder(unittest.TestCase):
    def setUp(self):
        self.pdf = test_pdf()
        self.uploads = {}

        def upload(key, data, content_type):
            self.uploads[key] = (data, content_type)

        self.upload = upload

    def bundle(self, structured=None, middle=None, assets=None):
        zip_data = make_zip(
            structured=structured or builder_structured(),
            middle=middle or builder_middle(),
            assets=assets or {"images/page_1_image_1.png": png_bytes()},
        )
        return MineruZip(zip_data, MineruConfig())

    def run_builder(self, **kwargs):
        bundle = self.bundle(**kwargs)
        with patch.object(mineru_layout, "MineruClient") as client_cls:
            client_cls.return_value.parse.return_value = bundle
            pages = build_pages(
                self.pdf,
                paper_id="p1",
                document_id="d1",
                upload=self.upload,
                on_stage=lambda *args: None,
            )
        return pages

    def test_special_content_becomes_images_and_markdown_is_clean(self):
        pages = self.run_builder()
        labels = [block["label"] for block in pages[0]["blocks"]]
        self.assertIn("table", labels)
        self.assertIn("algorithm", labels)
        self.assertIn("code", labels)
        self.assertIn("figure", labels)
        self.assertIn("equation", labels)

        content_key = "papers/p1/ocr/d1/content.md"
        markdown = self.uploads[content_key][0].decode()
        self.assertNotIn("<table", markdown.lower())
        self.assertNotIn("```python", markdown)
        self.assertIn("![Table]", markdown)
        self.assertIn("![Algorithm]", markdown)
        self.assertIn("![Code]", markdown)
        self.assertIn("![Figure]", markdown)
        self.assertIn("$$\nE = mc^2\n$$", markdown)
        self.assertIn("Table 1: A table.", markdown)
        self.assertEqual(markdown.count("Table 1: A table."), 1)

        order = [block["label"] for block in pages[0]["blocks"]]
        self.assertEqual(
            order,
            ["heading", "text", "table", "caption", "algorithm", "caption", "code", "figure", "caption", "equation"],
        )
        positions = [
            markdown.index("![Table]"),
            markdown.index("![Algorithm]"),
            markdown.index("![Code]"),
            markdown.index("![Figure]"),
        ]
        self.assertEqual(positions, sorted(positions))

        # Every Markdown image points at an uploaded object.
        import re

        links = re.findall(r"!\[[^\]]*\]\((assets/[^)]+)\)", markdown)
        self.assertEqual(len(links), 4)
        for link in links:
            key = f"papers/p1/ocr/d1/{link}"
            self.assertIn(key, self.uploads)

        table = next(b for b in pages[0]["blocks"] if b["label"] == "table")
        self.assertTrue(table["search_text"])
        self.assertTrue(table["image_key"].startswith("papers/p1/ocr/d1/assets/table_"))

    def test_caption_is_not_duplicated_when_inside_crop(self):
        structured = builder_structured()
        table = structured["pages"][0]["blocks"][2]
        table["captions"][0]["bbox"] = [0.2, 0.2, 0.4, 0.25]  # inside table bbox
        pages = self.run_builder(structured=structured)
        captions = [b["text"] for b in pages[0]["blocks"] if b["label"] == "caption"]
        self.assertNotIn("Table 1: A table.", captions)
        markdown = self.uploads["papers/p1/ocr/d1/content.md"][0].decode()
        self.assertNotIn("Table 1: A table.", markdown)

    def test_inline_html_is_converted_to_markdown(self):
        structured = builder_structured()
        structured["pages"][0]["blocks"][1]["content"] = "A <strong>bold</strong> word."
        self.run_builder(structured=structured)
        markdown = self.uploads["papers/p1/ocr/d1/content.md"][0].decode()
        self.assertIn("**bold**", markdown)
        self.assertNotIn("<strong>", markdown)

    def test_structured_reading_order_is_preserved(self):
        structured = {
            "pages": [
                {
                    "page_idx": 0,
                    "blocks": [
                        {"type": "text", "bbox": [0.1, 0.1, 0.45, 0.2], "content": "LEFT COLUMN"},
                        {"type": "text", "bbox": [0.55, 0.1, 0.9, 0.2], "content": "RIGHT COLUMN"},
                    ],
                }
            ]
        }
        self.run_builder(structured=structured, middle={"pages": []})
        markdown = self.uploads["papers/p1/ocr/d1/content.md"][0].decode()
        self.assertLess(markdown.index("LEFT COLUMN"), markdown.index("RIGHT COLUMN"))

    def test_prose_labelled_code_is_kept_as_text_with_diagnostic(self):
        structured = builder_structured()
        structured["pages"][0]["blocks"][4] = {
            "type": "code",
            "bbox": [0.1, 0.5, 0.55, 0.6],
            "content": "This is a prompt template with {rubric} and ordinary prose.",
        }
        middle = builder_middle()
        middle["pages"][0]["blocks"][4]["guess_lang"] = None
        pages = self.run_builder(structured=structured, middle=middle)
        prose = [
            b for b in pages[0]["blocks"] if "prompt template" in (b.get("text") or "")
        ]
        self.assertTrue(prose)
        self.assertTrue(any(d["needs_review"] for d in pages[0]["diagnostics"]))
        self.assertFalse(any(b["label"] == "code" for b in pages[0]["blocks"]))

    def test_missing_bbox_falls_back_to_mineru_image(self):
        structured = {
            "pages": [
                {
                    "page_idx": 0,
                    "blocks": [
                        {
                            "type": "table",
                            "content": "<table><td>x</td></table>",
                            "image_source": "images/table_fallback.png",
                        }
                    ],
                }
            ]
        }
        pages = self.run_builder(
            structured=structured,
            middle={"pages": []},
            assets={"images/table_fallback.png": png_bytes((0, 255, 0))},
        )
        table = pages[0]["blocks"][0]
        self.assertEqual(table["label"], "table")
        self.assertTrue(table["image_key"])

    def test_unrecoverable_table_keeps_text_and_flags_review(self):
        structured = table_structured(bbox=None)
        structured["pages"][0]["blocks"][0]["bbox"] = None
        pages = self.run_builder(structured=structured, middle={"pages": []}, assets={})
        self.assertEqual(pages[0]["blocks"][0]["label"], "text")
        self.assertIn("<table", pages[0]["blocks"][0]["text"])
        self.assertTrue(pages[0]["diagnostics"][0]["needs_review"])

    def test_cross_page_table_is_stacked_once(self):
        structured = {
            "pages": [
                {
                    "page_idx": 0,
                    "blocks": [
                        {
                            "type": "table",
                            "bbox": [0.1, 0.2, 0.5, 0.4],
                            "content": "<table><td>1</td></table>",
                            "captions": [{"bbox": [0.1, 0.41, 0.5, 0.44], "content": "Table 1: split."}],
                        }
                    ],
                },
                {
                    "page_idx": 1,
                    "blocks": [
                        {
                            "type": "table",
                            "bbox": [0.1, 0.1, 0.5, 0.3],
                            "continues_prev": True,
                            "content": "<table><td>2</td></table>",
                            "captions": [],
                        }
                    ],
                },
            ]
        }
        pages = self.run_builder(structured=structured, middle={"pages": []})
        table_blocks = [b for page in pages for b in page["blocks"] if b["label"] == "table"]
        self.assertEqual(len(table_blocks), 1)
        markdown = self.uploads["papers/p1/ocr/d1/content.md"][0].decode()
        self.assertEqual(markdown.count("![Table]"), 1)
        # The same key is written twice: first page crop, then the stacked image.
        key = table_blocks[0]["image_key"]
        self.assertGreaterEqual(sum(1 for k in self.uploads if k == key), 1)

    def test_rotated_page_crop_uses_displayed_bbox(self):
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=600)
        page.draw_rect(pymupdf.Rect(50, 50, 150, 100), color=(1, 0, 0), fill=(1, 0, 0))
        page.set_rotation(90)
        rotated = doc.tobytes()
        png = crop_pdf_region(rotated, 0, [500 / 600, 50 / 400, 550 / 600, 150 / 400])
        self.assertIsNotNone(png)
        image = Image.open(io.BytesIO(png)).convert("RGB")
        red, green, blue = image.getpixel((image.width // 2, image.height // 2))
        self.assertGreater(red, 180)
        self.assertLess(green, 80)
        self.assertLess(blue, 80)

    def test_missing_bbox_crop_returns_none(self):
        self.assertIsNone(crop_pdf_region(self.pdf, 0, None))
        self.assertIsNone(crop_pdf_region(self.pdf, 99, [0.1, 0.1, 0.2, 0.2]))


if __name__ == "__main__":
    unittest.main()
