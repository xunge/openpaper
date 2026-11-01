"""DeepSeek-OCR-2 client, page rasterisation, and block parsing.

The served model emits layout-annotated markdown:

    text[[73, 69, 492, 160]]
    Some paragraph…

    image[[90, 72, 901, 299]]

    figure_title[[350, 308, 644, 323]]
    Fig. 2: The Workflow of ALT4Decompile.

`image` blocks carry no text — they mark a figure region that has to be cropped
out of the rendered page, otherwise figures silently disappear from the reader.
"""

import base64
import io
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import pymupdf
import requests

from . import ocr_providers
from .otsl import convert_otsl_in_markdown, looks_like_otsl

logger = logging.getLogger(__name__)

OCR_BASE_URL = os.getenv("OCR_BASE_URL", "http://ocr:8000/v1")
OCR_MODEL = os.getenv("OCR_MODEL", "deepseek-ocr-2")
# The official runner renders at 144 DPI; at 200 the page needs more crops than
# the model handles well and the output starts to loop.
OCR_DPI = int(os.getenv("OCR_DPI", "144"))
# Layout boxes are normalised to 0-999, not pixels (see the official
# run_dpsk_ocr2_pdf.py: x1 / 999 * image_width).
BOX_SCALE = 999.0
OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "60"))
OCR_TIMEOUT_S = float(os.getenv("OCR_TIMEOUT_S", "300"))
OCR_MAX_TOKENS = int(os.getenv("OCR_MAX_TOKENS", "4096"))

OCR_PROMPT_LAYOUT = "<image>\n<|grounding|>Convert the document to markdown. "
# Fallback prompt: no layout annotation, used when the layout pass degenerates.
OCR_PROMPT_PLAIN = "<image>\nFree OCR. "

# `label[[x0, y0, x1, y1]]` — a block marker on its own line, sometimes with
# several boxes for a merged block.
_MARKER_LINE = re.compile(
    r"^([A-Za-z_]+)\s*(\[\[[-?\d,\s\[\]]+\]\])\s*$", re.MULTILINE
)
_MARKER_ANY = re.compile(r"\b[A-Za-z_]+\s*\[\[[-?\d,\s\[\]]+\]\]")
_BOX = re.compile(r"\[([-?\d]+)\s*,\s*([-?\d]+)\s*,\s*([-?\d]+)\s*,\s*([-?\d]+)\]")

FIGURE_LABELS = {"image", "figure", "picture", "chart"}


def boxes_from_marker(raw: str) -> List[List[int]]:
    return [[int(a), int(b), int(c), int(d)] for a, b, c, d in _BOX.findall(raw)]


def strip_markers(text: str) -> str:
    """Remove every layout marker, wherever it appears."""
    cleaned = _MARKER_ANY.sub("", text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def is_degenerate(text: str, source_len: int | None = None) -> bool:
    """Repetition loop detector shared by OCR and translation output."""
    if not text:
        return False
    if source_len and source_len > 80 and len(text) > source_len * 3.5:
        return True

    sentences = [s for s in re.split(r"[。！？.!?\n]", text) if len(s.strip()) > 12]
    if len(sentences) >= 4:
        unique = {s.strip() for s in sentences}
        if len(unique) / len(sentences) < 0.55:
            return True

    window = 16
    seen: Dict[str, int] = {}
    for start in range(0, max(0, len(text) - window), 8):
        gram = text[start:start + window]
        seen[gram] = seen.get(gram, 0) + 1
        if seen[gram] >= 3:
            return True
    return False


def render_page_images(
    pdf_bytes: bytes, dpi: int = OCR_DPI, max_pages: int = OCR_MAX_PAGES
) -> List[Tuple[int, bytes, int, int]]:
    """Rasterise pages. Returns [(page_index, png, width_px, height_px)]."""
    pages: List[Tuple[int, bytes, int, int]] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index in range(min(doc.page_count, max_pages)):
            page = doc.load_page(index)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pages.append((index, pixmap.tobytes("png"), pixmap.width, pixmap.height))
    logger.info("rendered %d page image(s) at %d DPI", len(pages), dpi)
    return pages


def crop_region(png_bytes: bytes, box: List[int], padding: int = 6) -> Optional[bytes]:
    """Cut a layout region out of the page image."""
    try:
        with pymupdf.open(stream=png_bytes, filetype="png") as image:
            page = image[0]
            x0, y0, x1, y1 = box
            rect = pymupdf.Rect(
                max(0, x0 - padding),
                max(0, y0 - padding),
                min(page.rect.width, x1 + padding),
                min(page.rect.height, y1 + padding),
            )
            if rect.is_empty or rect.width < 8 or rect.height < 8:
                return None
            pixmap = page.get_pixmap(clip=rect, dpi=150, alpha=False)
            return pixmap.tobytes("png")
    except Exception as exc:
        logger.warning("cropping %s failed: %s", box, exc)
        return None


def _call_ocr(
    png_bytes: bytes,
    prompt: str,
    base_url: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> str:
    payload = {
        "model": model or OCR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,"
                            + base64.b64encode(png_bytes).decode("ascii")
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0,
        "top_p": 1,
        "frequency_penalty": 0.2,
        "max_tokens": max_tokens or OCR_MAX_TOKENS,
    }
    response = requests.post(
        f"{(base_url or OCR_BASE_URL).rstrip('/')}/chat/completions", json=payload, timeout=OCR_TIMEOUT_S
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def parse_blocks(markdown: str) -> List[Dict]:
    """Split annotated markdown into blocks: {label, boxes, text}."""
    blocks: List[Dict] = []
    matches = list(_MARKER_LINE.finditer(markdown or ""))
    if not matches:
        text = strip_markers(markdown)
        return [{"label": "text", "boxes": [], "text": text}] if text else []

    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(markdown)
        body = strip_markers(markdown[match.end():end])
        blocks.append(
            {
                "label": match.group(1).lower(),
                "boxes": boxes_from_marker(match.group(2)),
                "text": body,
            }
        )
    return blocks


def ocr_page_with(provider_name: str, png_bytes: bytes) -> tuple[List[Dict], str, bool]:
    """Run the selected OCR backend on one page."""
    provider = ocr_providers.resolve(provider_name)

    if provider["layout"]:
        return ocr_page(png_bytes)

    raw = _call_ocr(
        png_bytes,
        provider["prompt"],
        base_url=provider["base_url"],
        model=provider["model"],
        max_tokens=provider.get("max_tokens"),
    )
    text = convert_otsl_in_markdown(raw) if looks_like_otsl(raw) else raw
    blocks = []
    for piece in ocr_providers.split_markdown_blocks(text):
        # Fragments cut off mid-token keep a stray `<f`/`<nl`; drop them.
        piece = re.sub(r"<[a-z]{0,6}$", "", piece).strip()
        if piece:
            blocks.append({"label": "text", "boxes": [], "text": piece})
    # Judge repetition on the prose only: table markup repeats the same tags and
    # would trip the n-gram detector on perfectly good output.
    joined = " ".join(b["text"] for b in blocks)
    plain = re.sub(r"<[^>]+>", " ", joined)
    return blocks, raw, is_degenerate(plain)


def ocr_page(png_bytes: bytes) -> tuple[List[Dict], str, bool]:
    """OCR one page.

    Returns (blocks, raw_output, degenerate). The raw model output is preserved so
    the reader can show exactly what OCR produced; a repetition loop is reported
    rather than silently dropping the page, because a missing page looks like a
    bug in the pipeline.
    """
    raw = _call_ocr(png_bytes, OCR_PROMPT_LAYOUT)
    blocks = parse_blocks(raw)
    joined = " ".join(b["text"] for b in blocks)

    if is_degenerate(joined):
        logger.warning("OCR output degenerated; retrying with the plain prompt")
        retry = _call_ocr(png_bytes, OCR_PROMPT_PLAIN)
        retry_blocks = parse_blocks(retry)
        if not is_degenerate(" ".join(b["text"] for b in retry_blocks)):
            return retry_blocks, retry, False
        return blocks, raw, True

    return blocks, raw, False


def ocr_available() -> bool:
    """Cheap readiness probe used by the API before queueing a document."""
    try:
        return requests.get(f"{OCR_BASE_URL}/models", timeout=5).status_code == 200
    except Exception:
        return False


def crop_region_pdf(pdf_bytes: bytes, page_index: int, rect_pts: List[float], dpi: int = 200) -> Optional[bytes]:
    """Crop a figure region straight from the PDF at print resolution."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc.load_page(page_index)
            rect = pymupdf.Rect(*rect_pts) & page.rect
            if rect.is_empty or rect.width < 8 or rect.height < 8:
                return None
            pixmap = page.get_pixmap(clip=rect, dpi=dpi, alpha=False)
            return pixmap.tobytes("png")
    except Exception as exc:
        logger.warning("cropping figure %s failed: %s", rect_pts, exc)
        return None


def crop_region_norm(png_bytes: bytes, box_norm: List[float], padding: int = 4) -> Optional[bytes]:
    """Crop using a 0-1 normalised box against the rendered page image."""
    try:
        with pymupdf.open(stream=png_bytes, filetype="png") as image:
            page = image[0]
            width, height = page.rect.width, page.rect.height
            rect = pymupdf.Rect(
                max(0.0, box_norm[0] * width - padding),
                max(0.0, box_norm[1] * height - padding),
                min(width, box_norm[2] * width + padding),
                min(height, box_norm[3] * height + padding),
            )
            if rect.is_empty or rect.width < 8 or rect.height < 8:
                return None
            return page.get_pixmap(clip=rect, dpi=150, alpha=False).tobytes("png")
    except Exception as exc:
        logger.warning("normalised crop failed for %s: %s", box_norm, exc)
        return None


def available_providers() -> List[Dict]:
    """Which OCR backends answer right now."""
    result = []
    for name, provider in ocr_providers.PROVIDERS.items():
        try:
            health_path = provider.get("health_path", "/models")
            ok = (
                requests.get(
                    f"{provider['base_url'].rstrip('/')}{health_path}", timeout=4
                ).status_code
                == 200
            )
        except Exception:
            ok = False
        result.append(
            {
                "name": name,
                "label": provider["label"],
                "description": provider["description"],
                "available": ok,
            }
        )
    return result
