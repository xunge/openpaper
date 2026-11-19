import json
import unittest
from unittest.mock import Mock, patch

from app.helpers.deepseek_venue import VenueLookupError, lookup_venue


def _response(content: str, status: int = 200):
    response = Mock()
    response.status_code = status
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


class TestDeepSeekVenueLookup(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            "os.environ",
            {
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
                "DEEPSEEK_FAST_MODEL": "deepseek-flash",
                "DEEPSEEK_VENUE_ENABLED": "true",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @patch("app.helpers.deepseek_venue.requests.post")
    def test_returns_venue_and_year(self, post):
        post.return_value = _response(json.dumps({"venue": "NDSS Symposium", "year": 2026}))
        result = lookup_venue(title="Example paper", authors=["A. Author"])
        self.assertEqual(result.venue, "NDSS Symposium")
        self.assertEqual(result.year, 2026)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["tools"][0]["type"], "deepseek_web_search")
        self.assertIn("只返回一个 JSON", payload["messages"][0]["content"])

    @patch("app.helpers.deepseek_venue.requests.post")
    def test_accepts_fenced_json(self, post):
        post.return_value = _response('```json\n{"venue": "Nature", "year": 2024}\n```')
        result = lookup_venue(title="Example paper")
        self.assertEqual(result.venue, "Nature")
        self.assertEqual(result.year, 2024)

    @patch("app.helpers.deepseek_venue.requests.post")
    def test_null_means_not_found(self, post):
        post.return_value = _response('{"venue": null, "year": null}')
        result = lookup_venue(title="Unknown paper")
        self.assertIsNone(result.venue)
        self.assertIsNone(result.year)

    @patch("app.helpers.deepseek_venue.requests.post")
    def test_invalid_year_is_dropped(self, post):
        post.return_value = _response('{"venue": "Some Journal", "year": 1800}')
        result = lookup_venue(title="Example paper")
        self.assertEqual(result.venue, "Some Journal")
        self.assertIsNone(result.year)

    @patch("app.helpers.deepseek_venue.requests.post")
    def test_non_json_retries_once_then_raises(self, post):
        post.return_value = _response("I could not find it.")
        with self.assertRaises(VenueLookupError):
            lookup_venue(title="Example paper")
        self.assertEqual(post.call_count, 2)

    def test_missing_title_raises(self):
        with self.assertRaises(VenueLookupError):
            lookup_venue(title="")

    def test_disabled_raises(self):
        with patch.dict("os.environ", {"DEEPSEEK_VENUE_ENABLED": "false"}):
            with self.assertRaises(VenueLookupError):
                lookup_venue(title="Example paper")


if __name__ == "__main__":
    unittest.main()
