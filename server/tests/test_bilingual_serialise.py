"""Serialisation tests for the bilingual API (MinerU debugging URLs)."""

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app.api.paper_bilingual_api import _serialise


def fake_document(ocr_model="mineru"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        paper_id=uuid.uuid4(),
        status="completed",
        progress=1,
        page_count=1,
        stage="done",
        stage_progress=1,
        stage_total=1,
        error=None,
        ocr_model=ocr_model,
        translation_model="deepseek-flash",
        pages=[
            {
                "index": 0,
                "image_key": None,
                "zh_md": "中文",
                "en_md": "English",
                "raw_ocr": "",
                "ocr_degenerate": False,
                "search_text": "searchable",
                "diagnostics": [{"page": 0, "needs_review": True}],
                "blocks": [
                    {
                        "id": "0-0",
                        "label": "table",
                        "boxes_norm": [[0.1, 0.1, 0.2, 0.2]],
                        "en_md": "",
                        "zh_md": "",
                        "image_key": "papers/p/ocr/d/assets/table_001.png",
                        "search_text": "table content",
                        "needs_review": False,
                    }
                ],
            }
        ],
    )


class TestBilingualSerialise(unittest.TestCase):
    def test_mineru_document_exposes_content_source_and_search_text(self):
        doc = fake_document()
        with patch(
            "app.api.paper_bilingual_api.s3_service.generate_presigned_url",
            side_effect=lambda object_key, expiration: f"https://s3/{object_key}",
        ):
            payload = _serialise(doc)

        self.assertEqual(
            payload["content_url"],
            f"https://s3/papers/{doc.paper_id}/ocr/{doc.id}/content.md",
        )
        self.assertIn("structured_content", payload["source_urls"])
        self.assertEqual(payload["pages"][0]["diagnostics"][0]["needs_review"], True)
        block = payload["pages"][0]["blocks"][0]
        self.assertEqual(block["search_text"], "table content")
        self.assertEqual(
            block["image_url"],
            "https://s3/papers/p/ocr/d/assets/table_001.png",
        )

    def test_legacy_document_has_no_mineru_urls(self):
        payload = _serialise(fake_document(ocr_model="deepseek-ocr-2"))
        self.assertIsNone(payload["content_url"])
        self.assertIsNone(payload["source_urls"])


if __name__ == "__main__":
    unittest.main()
