"""Recover tables whose cell boundaries the OCR dropped.

DeepSeek-OCR sometimes writes a whole table as one glued run of text
(`<table>SccDec-6.7B18.939.6…`), which no amount of HTML fixing can turn back into
a grid. For those, the model is asked to rebuild a markdown table from the same
content — the numbers are all there, only the boundaries are missing.
"""

import logging
import os
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
MODEL = os.getenv("TABLE_REBUILD_MODEL") or os.getenv("DEEPSEEK_FAST_MODEL", "deepseek-flash")

PROMPT = """下面是从论文 PDF 的表格中提取出来的内容，单元格边界已经丢失。
请把它还原成一个规范的 markdown 表格。

要求：
1. 只使用下面提供的文字和数字，不要编造任何数值或文字。
2. 表头行用 | 分隔；分隔行用 |---|---|。
3. 每一行的列数必须一致。
4. 只输出 markdown 表格，不要解释、不要代码块围栏。

内容：
---
{content}
---"""

_MIN_USEFUL_CELLS = 4


def needs_rebuild(table_html: str) -> bool:
    """True when the markup has too few cells for its content."""
    cells = len(re.findall(r"<t[dh]\b", table_html, re.IGNORECASE))
    text = re.sub(r"<[^>]+>", " ", table_html)
    return cells < _MIN_USEFUL_CELLS and len(text.strip()) > 200


def rebuild_table(table_html: str) -> Optional[str]:
    """Ask the model for a markdown table; None when it cannot produce one."""
    if not API_KEY:
        return None
    content = re.sub(r"</?(table|tr|td|th|tbody|thead)[^>]*>", " ", table_html, flags=re.IGNORECASE)
    content = " ".join(content.split())[:4000]

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是表格结构还原器，只输出 markdown 表格。"},
            {"role": "user", "content": PROMPT.format(content=content)},
        ],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 4096,
    }
    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=120,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"] or ""
    except Exception as exc:
        logger.warning("table rebuild failed: %s", exc)
        return None

    text = text.strip().strip("`")
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return None
    # Every row must have the same number of columns.
    widths = {len([c for c in line.strip("|").split("|")]) for line in lines}
    if len(widths) != 1:
        logger.warning("rebuilt table has ragged columns: %s", widths)
        return None
    return "\n".join(lines)
