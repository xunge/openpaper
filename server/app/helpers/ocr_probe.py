"""Which OCR backends the reader can choose from.

The pipeline itself lives in the jobs worker; the server only needs the display
metadata and a cheap readiness probe so the picker can disable an offline model.
Keep the provider names in sync with ``jobs/src/bilingual/ocr_providers.py``.
"""

import logging
import os
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

PROVIDERS: Dict[str, Dict] = {
    "deepseek-ocr-2": {
        "label": "DeepSeek-OCR-2",
        "description": "版面标记 + 坐标定位，表格为 HTML",
        "base_url": os.getenv("OCR_BASE_URL", "http://ocr:8000/v1"),
        "health_path": "/models",
    },
    "xiaomi-ocr-0": {
        "label": "Xiaomi-OCR-0",
        "description": "Qwen3.5-VL 底座，表格为 OTSL，公式 LaTeX",
        "base_url": os.getenv("XIAOMI_OCR_BASE_URL", "http://ocr-xiaomi:8000/v1"),
        "health_path": "/models",
    },
    "mineru": {
        "label": "MinerU 4.1",
        "description": "结构化版面，表格/算法/代码按原 PDF 切图",
        "base_url": os.getenv(
            "MINERU_FILE_PARSE_URL", "http://127.0.0.1:8003"
        ).rstrip("/"),
        "health_path": "/openapi.json",
    },
}

DEFAULT_PROVIDER = os.getenv("OCR_PROVIDER", "mineru")


def resolve(name: str | None) -> Dict:
    key = (name or DEFAULT_PROVIDER).strip()
    if key in PROVIDERS:
        return {"name": key, **PROVIDERS[key]}
    return {"name": DEFAULT_PROVIDER, **PROVIDERS[DEFAULT_PROVIDER]}


def _ready(base_url: str, health_path: str = "/models") -> bool:
    try:
        return (
            requests.get(
                f"{base_url.rstrip('/')}{health_path}", timeout=4
            ).status_code
            == 200
        )
    except requests.RequestException:
        return False


def available_providers() -> List[Dict]:
    return [
        {
            "name": name,
            "label": provider["label"],
            "description": provider["description"],
            "available": _ready(
                provider["base_url"], provider.get("health_path", "/models")
            ),
        }
        for name, provider in PROVIDERS.items()
    ]


def ocr_available() -> bool:
    provider = resolve(None)
    return _ready(provider["base_url"], provider.get("health_path", "/models"))
