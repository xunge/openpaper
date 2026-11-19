"""DeepSeek web-search lookup for a paper's publication venue.

This is the only source used to fill a missing venue for newly uploaded papers.
It deliberately does not parse the PDF: the caller supplies the title and authors,
and DeepSeek decides what to search. No API key ever leaves the server process.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class VenueLookupResult:
    venue: Optional[str]
    year: Optional[int]
    model: str


class VenueLookupError(RuntimeError):
    """The lookup could not be completed; callers should leave the venue empty."""


def _enabled() -> bool:
    return os.getenv("DEEPSEEK_VENUE_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _base_url() -> str:
    base = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    return base or DEFAULT_BASE_URL


def _model() -> str:
    return (
        os.getenv("DEEPSEEK_VENUE_MODEL")
        or os.getenv("DEEPSEEK_FAST_MODEL")
        or DEFAULT_MODEL
    )


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _clean_venue(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    venue = " ".join(value.split()).strip().strip("\"'")
    if not venue or venue.lower() in {"null", "none", "unknown", "n/a", "na"}:
        return None
    if len(venue) < 2 or len(venue) > 200:
        return None
    if venue.startswith(("http://", "https://")):
        return None
    return venue


def _clean_year(value: object) -> Optional[int]:
    try:
        year = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    current_year = time.gmtime().tm_year
    if 1900 <= year <= current_year + 1:
        return year
    return None


def _prompt(title: str, authors: list[str], doi: Optional[str]) -> str:
    author_line = ", ".join(authors) if authors else "unknown"
    doi_line = doi or "unknown"
    return (
        "请联网核实下面这篇论文正式发表的期刊或会议，并给出发表年份。\n\n"
        f"标题：{title.strip()}\n"
        f"作者：{author_line}\n"
        f"DOI：{doi_line}\n\n"
        "只返回一个 JSON 对象，不要解释、不要 Markdown、不要 URL。格式：\n"
        '{"venue": "期刊名或会议名", "year": 2024}\n\n'
        "规则：\n"
        "1. 优先正式发表版本；如果只有预印本，返回预印本平台名称。\n"
        "2. 必须匹配标题和作者，不能从参考文献中选择期刊/会议。\n"
        "3. 无法确认时返回：{\"venue\": null, \"year\": null}\n"
        "4. venue 使用期刊/会议的正式全称或通用缩写，不要包含出版社、DOI 或链接。"
    )


def lookup_venue(
    *,
    title: Optional[str],
    authors: Optional[list[str]] = None,
    doi: Optional[str] = None,
) -> VenueLookupResult:
    """Ask DeepSeek with web search for the paper's venue and year.

    Raises VenueLookupError on configuration/API/parse failure so the caller can
    log and continue without failing the upload. A valid "not found" response
    returns `venue=None`.
    """
    if not _enabled():
        raise VenueLookupError("DeepSeek venue lookup is disabled")
    if not title or not title.strip():
        raise VenueLookupError("title is required for venue lookup")

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise VenueLookupError("DEEPSEEK_API_KEY is not configured")

    model = _model()
    payload = {
        "model": model,
        "thinking": {"type": "enabled"},
        "tools": [
            {"type": "deepseek_web_search"},
            {"type": "deepseek_read_url"},
        ],
        "messages": [
            {
                "role": "user",
                "content": _prompt(title, authors or [], doi),
            }
        ],
        # Tool calls work without streaming; collecting one JSON body is simpler
        # and keeps this off the hot path.
        "stream": False,
        "temperature": 0,
        "max_tokens": 1024,
    }

    timeout = float(os.getenv("DEEPSEEK_VENUE_TIMEOUT", str(DEFAULT_TIMEOUT)))
    last_error: Optional[str] = None
    for attempt in range(1, 3):
        try:
            response = requests.post(
                f"{_base_url()}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = f"network error: {exc.__class__.__name__}"
            logger.warning("DeepSeek venue lookup attempt %s failed: %s", attempt, exc)
            time.sleep(2)
            continue

        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
            last_error = f"HTTP {response.status_code}"
            logger.warning(
                "DeepSeek venue lookup attempt %s failed with HTTP %s",
                attempt,
                response.status_code,
            )
            time.sleep(2)
            continue
        if response.status_code != 200:
            raise VenueLookupError(
                f"DeepSeek venue lookup failed: HTTP {response.status_code}"
            )

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise VenueLookupError("DeepSeek returned an unexpected response") from exc

        parsed = _extract_json(content)
        if parsed is None:
            last_error = "response was not valid JSON"
            logger.warning(
                "DeepSeek venue lookup attempt %s returned non-JSON: %r",
                attempt,
                (content or "")[:200],
            )
            if attempt < 2:
                payload["messages"][0]["content"] += "\n\n只输出 JSON，不要解释，不要 Markdown。"
                continue
            raise VenueLookupError("DeepSeek did not return valid JSON")

        venue = _clean_venue(parsed.get("venue"))
        year = _clean_year(parsed.get("year"))
        logger.info(
            "DeepSeek venue lookup for %r -> venue=%r year=%r (model=%s)",
            title[:100],
            venue,
            year,
            model,
        )
        return VenueLookupResult(venue=venue, year=year, model=model)

    raise VenueLookupError(last_error or "DeepSeek venue lookup failed")
