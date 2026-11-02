"""MinerU OCR readiness probe for the bilingual reader.

The pipeline lives in the jobs worker. The server only needs a cheap readiness
check to disable the "start OCR" action when MinerU is unreachable.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

MINERU_FILE_PARSE_URL = os.getenv(
    "MINERU_FILE_PARSE_URL", "http://127.0.0.1:8003"
).rstrip("/")


def ocr_available() -> bool:
    try:
        return (
            requests.get(
                f"{MINERU_FILE_PARSE_URL}/openapi.json", timeout=4
            ).status_code
            == 200
        )
    except requests.RequestException:
        return False
