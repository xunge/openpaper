"""OCR provider registry.

Two backends are served side by side, both behind an OpenAI-compatible endpoint:

* `deepseek-ocr-2` — layout-annotated markdown with `label[[x0,y0,x1,y1]]` markers
  whose boxes are normalised to 0-999.
* `xiaomi-ocr-0` — plain markdown with OTSL tables and LaTeX formulas, no layout
  markers; positions are recovered from the PDF text layer instead.
"""

import os
import re
from typing import Dict, List

DEEPSEEK_BASE = os.getenv("OCR_BASE_URL", "http://ocr:8000/v1")
XIAOMI_BASE = os.getenv("XIAOMI_OCR_BASE_URL", "http://ocr-xiaomi:8000/v1")
MINERU_BASE = os.getenv("MINERU_FILE_PARSE_URL", "http://127.0.0.1:8003")

DEEPSEEK_PROMPT = "<image>\n<|grounding|>Convert the document to markdown. "
XIAOMI_PROMPT = (
    "Extract all information from the main body of the document image and represent "
    "it in markdown format, ignoring headers and footers. Tables should be expressed "
    "in OTSL format, formulas in the document should be represented using LATEX "
    "format, and the parsing should be organized according to the reading order."
)

PROVIDERS: Dict[str, Dict] = {
    "deepseek-ocr-2": {
        "label": "DeepSeek-OCR-2",
        "base_url": DEEPSEEK_BASE,
        "model": os.getenv("OCR_MODEL", "deepseek-ocr-2"),
        "prompt": DEEPSEEK_PROMPT,
        "layout": True,
        "max_tokens": 4096,
        "description": "版面标记 + 坐标定位，表格为 HTML",
    },
    "xiaomi-ocr-0": {
        "label": "Xiaomi-OCR-0",
        "base_url": XIAOMI_BASE,
        "model": os.getenv("XIAOMI_OCR_MODEL", "xiaomi-ocr-0"),
        "prompt": XIAOMI_PROMPT,
        "layout": False,
        # Dense pages produce long OTSL tables; the default budget truncates them.
        "max_tokens": 6144,
        "description": "Qwen3.5-VL 底座，表格为 OTSL，公式 LaTeX",
    },
}

PROVIDERS["mineru"] = {
    "label": "MinerU 4.1",
    "base_url": MINERU_BASE,
    "model": "mineru",
    "prompt": "",
    "layout": True,
    # MinerU is document-level; pipeline.generate_pages takes a dedicated branch.
    "document_level": True,
    "health_path": "/openapi.json",
    "description": "结构化版面，表格/算法/代码按原 PDF 切图",
}

DEFAULT_PROVIDER = os.getenv("OCR_PROVIDER", "mineru")

_HEADING = re.compile(r"^(#{1,6})\s+")


def resolve(name: str | None) -> Dict:
    key = (name or DEFAULT_PROVIDER).strip()
    if key in PROVIDERS:
        return {"name": key, **PROVIDERS[key]}
    return {"name": DEFAULT_PROVIDER, **PROVIDERS[DEFAULT_PROVIDER]}


def split_markdown_blocks(markdown: str) -> List[str]:
    """Split plain markdown into blocks on headings and blank lines."""
    blocks: List[str] = []
    buffer: List[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            blocks.append(text)
        buffer.clear()

    for line in (markdown or "").splitlines():
        if _HEADING.match(line.strip()):
            flush()
            buffer.append(line)
            continue
        if not line.strip():
            flush()
            continue
        buffer.append(line)
    flush()
    return blocks
