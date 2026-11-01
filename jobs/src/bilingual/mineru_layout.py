"""MinerU structured layout -> OpenPaper bilingual pages.

MinerU is a document-level parser: one call returns the whole PDF as structured
blocks plus region images. The rest of the bilingual pipeline (translation,
cross-page paragraph merge, page callbacks) still works on the same page/block
shape, so this module is a *builder*, not a second pipeline.

Special content is never rendered as HTML/code text: tables, algorithms and code
are cropped from the original PDF at 300 DPI, and only the image (plus a
translatable caption) reaches the reader Markdown. MinerU's own region JPEG is
only a fallback when a PDF crop is impossible (missing bbox/out-of-range page).
"""

from __future__ import annotations

import html as html_module
import io
import json
import logging
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pymupdf
from PIL import Image

from .mineru_client import MineruClient, MineruError, MineruZip

logger = logging.getLogger(__name__)

MINERU_DPI = int(os.getenv("MINERU_DPI", "300"))
CROP_PADDING_PT = float(os.getenv("MINERU_CROP_PADDING_PT", "2"))
PRESERVE_VISUAL = os.getenv("MINERU_PRESERVE_VISUAL_BLOCKS", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
VISUAL_TYPES = {
    item.strip().lower()
    for item in os.getenv("MINERU_VISUAL_BLOCK_TYPES", "table,algorithm,code").split(",")
    if item.strip()
}

SKIP_TYPES = {"header", "footer", "page_number", "aside_text", "page_header"}
TEXT_TYPES = {"text", "ref_text", "page_footnote", "list"}
HEADING_TYPES = {"doc_title": 1, "paragraph_title": 2}

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_FENCE = re.compile(r"^\s*```")
_ANY_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*\b[^>]*>")


def _sanitize_markdown(text: str) -> str:
    """Turn MinerU's inline HTML into Markdown.

    The reader deliberately has no `rehype-raw` (untrusted OCR HTML must not be
    rendered), so `<strong>`/`<sup>` would otherwise appear literally. Tables are
    images by this point; only inline formatting remains.
    """
    value = text or ""
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:strong|b)>", "**", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:em|i)>", "*", value, flags=re.IGNORECASE)
    value = re.sub(r"</?code>", "`", value, flags=re.IGNORECASE)
    value = re.sub(
        r"<sup>(.*?)</sup>", r"^{\1}", value, flags=re.IGNORECASE | re.DOTALL
    )
    value = re.sub(
        r"<sub>(.*?)</sub>", r"_{\1}", value, flags=re.IGNORECASE | re.DOTALL
    )
    value = _ANY_TAG.sub("", value)
    return value


def _plain_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(html_module.unescape(text).split())


def _plain_content(text: str) -> str:
    """Text used for search/caption comparison, not for reading Markdown."""
    value = (text or "").strip()
    if "<table" in value.lower():
        return _plain_html(value)
    return value


def _strip_fences(text: str) -> str:
    lines = (text or "").strip().splitlines()
    if lines and _FENCE.match(lines[0]):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _bbox(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if not all(0.0 <= v <= 1.0 for v in box):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _caption_list(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    captions = []
    for caption in block.get("captions") or []:
        if isinstance(caption, str):
            text, box = caption, None
        elif isinstance(caption, dict):
            content = caption.get("content")
            if isinstance(content, list):
                text = " ".join(
                    str(part.get("content", "")) for part in content if isinstance(part, dict)
                )
            else:
                text = str(content or "")
            box = _bbox(caption.get("bbox"))
        else:
            continue
        text = text.strip()
        if text:
            captions.append({"text": text, "bbox": box})
    return captions


def _caption_overlaps(box: Optional[List[float]], image_box: Optional[List[float]]) -> bool:
    if not box or not image_box:
        return False
    ix0, iy0 = max(box[0], image_box[0]), max(box[1], image_box[1])
    ix1, iy1 = min(box[2], image_box[2]), min(box[3], image_box[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    area = (box[2] - box[0]) * (box[3] - box[1])
    if area <= 0:
        return False
    return ((ix1 - ix0) * (iy1 - iy0)) / area > 0.8


def crop_pdf_region(
    pdf_bytes: bytes,
    page_index: int,
    bbox: Optional[List[float]],
    dpi: int = MINERU_DPI,
    padding_pt: float = CROP_PADDING_PT,
) -> Optional[bytes]:
    """Crop a normalized MinerU bbox from the original PDF as PNG.

    PyMuPDF's `page.rect` is the *displayed* rectangle (rotation applied) and
    `get_pixmap(clip=...)` accepts displayed coordinates, which is also what
    MinerU's normalized bbox refers to. No manual rotation transform is needed.
    """
    box = _bbox(bbox)
    if box is None:
        return None
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_index < 0 or page_index >= doc.page_count:
                return None
            page = doc.load_page(page_index)
            width, height = page.rect.width, page.rect.height  # rotation applied
            clip = pymupdf.Rect(
                max(0.0, box[0] * width - padding_pt),
                max(0.0, box[1] * height - padding_pt),
                min(width, box[2] * width + padding_pt),
                min(height, box[3] * height + padding_pt),
            )
            if clip.width < 2 or clip.height < 2:
                return None
            pixmap = page.get_pixmap(clip=clip, dpi=dpi, alpha=False)
            if pixmap.width < 4 or pixmap.height < 4:
                return None
            return pixmap.tobytes("png")
    except Exception as exc:
        logger.warning("crop page %s bbox %s failed: %s", page_index, bbox, exc)
        return None


def _image_from_bundle(bundle: MineruZip, image_source: Optional[str]) -> Optional[bytes]:
    if not image_source:
        return None
    try:
        raw = bundle.read(image_source)
    except MineruError as exc:
        logger.warning("MinerU image %s unreadable: %s", image_source, exc)
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", dpi=(MINERU_DPI, MINERU_DPI))
        return buffer.getvalue()
    except Exception as exc:
        logger.warning("converting MinerU image %s failed: %s", image_source, exc)
        return None


def _stack_png(images: List[bytes]) -> Optional[bytes]:
    try:
        opened = [Image.open(io.BytesIO(data)).convert("RGB") for data in images]
        width = max(image.width for image in opened)
        gap = 12
        height = sum(image.height for image in opened) + gap * (len(opened) - 1)
        canvas = Image.new("RGB", (width, height), "white")
        y = 0
        for image in opened:
            canvas.paste(image, ((width - image.width) // 2, y))
            y += image.height + gap
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", dpi=(MINERU_DPI, MINERU_DPI))
        return buffer.getvalue()
    except Exception as exc:
        logger.warning("stacking cross-page images failed: %s", exc)
        return None


def _looks_like_algorithm(text: str, captions: List[Dict[str, Any]]) -> bool:
    value = _strip_fences(text)
    opening = "\n".join(value.splitlines()[:8])
    if re.search(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:algorithm|procedure|pseudocode)\b", opening
    ):
        return True
    if any(
        re.search(r"(?i)^\s*(?:algorithm|procedure)\s*\d+", c["text"])
        for c in captions
    ):
        return True
    # Pseudocode that opens with Require/Ensure rather than the word Algorithm.
    if re.search(r"(?im)^\s*(?:require|input)\s*:", opening) and re.search(
        r"(?im)^\s*(?:ensure|output)\s*:", opening
    ):
        return True
    return False


_PSEUDOCODE_LINE = re.compile(
    r"(?im)^\s*(?:(?:if|then|else|for|while|return|end|procedure|function)\b|//)"
)


def _looks_like_pseudocode(text: str) -> bool:
    return bool(_PSEUDOCODE_LINE.search(_strip_fences(text)))


def _looks_like_code(text: str, guess_lang: Optional[str], sub_type: Optional[str]) -> bool:
    value = _strip_fences(text)
    if not value.strip():
        return False
    language = (guess_lang or "").lower()
    if language and language not in {"txt", "text", "unknown"}:
        return True
    match = re.match(r"```\s*([A-Za-z0-9+#.-]+)", text.lstrip())
    if match and match.group(1).lower() not in {"txt", "text"}:
        return True

    cues = 0
    for pattern in (
        r"(?m)^\s*#\s*include\b",
        r"(?m)^\s*(?:def|class)\s+\w+",
        r"(?m)^\s*(?:import|from)\s+\w+",
        r"(?m)^\s*(?:function|func|fn)\s+\w+",
        r"(?m)^\s*(?:for|while|if)\s*\(",
        r"(?m)^\s*(?:public|private|static)\s+",
        r"(?m)^\s*(?:int|void|char|float|double)\s+\w+\s*\(",
        r"\bprintf\s*\(",
        r"\bstd::",
        r"(?m)^\s*[{}]\s*$",
        r";\s*$",
        r"(?m)^\s*//",
        r"(?m)^\s*/\*",
    ):
        if re.search(pattern, value):
            cues += 1
    if sub_type == "code" and _FENCE.match(text.lstrip()) and cues >= 1:
        return True
    return cues >= 2


def _heading_markdown(text: str, level: int) -> str:
    clean = re.sub(r"^\s*#{1,6}\s+", "", text or "").strip()
    return f"{'#' * max(1, min(level, 6))} {clean}" if clean else ""


def _join_caption_text(captions: List[Dict[str, Any]]) -> str:
    return "\n\n".join(caption["text"] for caption in captions)


class _Builder:
    def __init__(
        self,
        pdf_bytes: bytes,
        paper_id: str,
        document_id: str,
        bundle: MineruZip,
        upload: Any,
    ) -> None:
        self.pdf_bytes = pdf_bytes
        self.paper_id = paper_id
        self.document_id = document_id
        self.bundle = bundle
        self.upload = upload
        self.prefix = f"papers/{paper_id}/ocr/{document_id}"
        self.counters: Counter = Counter()
        self.pages: List[Dict[str, Any]] = []
        self.last_visual: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------- assets
    def _key(self, kind: str, page_index: int) -> str:
        self.counters[kind] += 1
        return (
            f"{self.prefix}/assets/{kind}_{page_index + 1:03d}_"
            f"{self.counters[kind]:03d}.png"
        )

    def _upload_png(self, key: str, data: bytes) -> None:
        self.upload(key, data, "image/png")

    def _make_visual(
        self,
        kind: str,
        page_index: int,
        block: Dict[str, Any],
        captions: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        box = _bbox(block.get("bbox"))
        data = crop_pdf_region(self.pdf_bytes, page_index, box)
        image_source = block.get("image_source")
        if data is None:
            data = _image_from_bundle(self.bundle, image_source)
        if data is None:
            return None
        key = self._key(kind, page_index)
        self._upload_png(key, data)
        search = _plain_content(str(block.get("content") or ""))
        caption_text = _join_caption_text(captions)
        if caption_text:
            search = f"{search}\n\n{caption_text}".strip()
        return {
            "label": kind,
            "text": "",
            "boxes_norm": [box] if box else [],
            "image_key": key,
            "search_text": search[:20000],
            "asset_kind": kind,
            "png": data,
            "page_index": page_index,
        }

    # ------------------------------------------------------------- pages
    def build(self, structured: Dict[str, Any], middle: Dict[str, Any]) -> List[Dict[str, Any]]:
        middle_pages = {page.get("page_idx"): page.get("blocks", []) for page in middle.get("pages", [])}
        try:
            with pymupdf.open(stream=self.pdf_bytes, filetype="pdf") as doc:
                page_count = doc.page_count
        except Exception:
            page_count = len(structured.get("pages", []))
        if not page_count:
            raise MineruError("MinerU structured_content.json has no pages")

        for page in sorted(structured.get("pages", []), key=lambda p: p.get("page_idx", 0)):
            page_index = int(page.get("page_idx", len(self.pages)))
            blocks_out: List[Dict[str, Any]] = []
            diagnostics: List[Dict[str, Any]] = []
            middle_blocks = middle_pages.get(page_index, [])
            for position, block in enumerate(page.get("blocks", [])):
                result = self._emit_block(
                    page_index,
                    position,
                    block,
                    middle_blocks[position] if position < len(middle_blocks) else {},
                    diagnostics,
                )
                blocks_out.extend(result)
            self.pages.append(
                {
                    "index": page_index,
                    "image_key": None,
                    "blocks": blocks_out,
                    "raw": "",
                    "degenerate": False,
                    "diagnostics": diagnostics,
                    "search_text": "\n\n".join(
                        b.get("search_text", "") for b in blocks_out if b.get("search_text")
                    )[:20000],
                }
            )
        # Preserve a page entry even when a page had no recoverable blocks.
        seen = {page["index"] for page in self.pages}
        for page_index in range(page_count):
            if page_index not in seen:
                self.pages.append(
                    {
                        "index": page_index,
                        "image_key": None,
                        "blocks": [],
                        "raw": "",
                        "degenerate": False,
                        "diagnostics": [],
                        "search_text": "",
                    }
                )
        self.pages.sort(key=lambda p: p["index"])
        return self.pages

    def _emit_block(
        self,
        page_index: int,
        position: int,
        block: Dict[str, Any],
        middle_block: Dict[str, Any],
        diagnostics: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        block_type = str(block.get("type") or middle_block.get("type") or "text").lower()
        content = block.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("content", "")) for part in content if isinstance(part, dict)
            )
        content = str(content or "")
        captions = _caption_list(block) or _caption_list(middle_block)
        box = _bbox(block.get("bbox")) or _bbox(middle_block.get("bbox"))
        merged = dict(block)
        merged.setdefault("bbox", box)
        merged.setdefault("image_source", middle_block.get("image_source"))
        merged.setdefault("sub_type", block.get("sub_type") or middle_block.get("sub_type"))
        merged.setdefault("guess_lang", middle_block.get("guess_lang"))
        merged["content"] = content

        if block_type in SKIP_TYPES:
            return []

        if block_type == "equation":
            latex = content.strip()
            latex = re.sub(r"^\s*\$\$|\$\$\s*$", "", latex).strip()
            if latex:
                return [
                    {
                        "label": "equation",
                        "text": f"$$\n{latex}\n$$",
                        "boxes_norm": [box] if box else [],
                    }
                ]
            return []

        if block_type in HEADING_TYPES:
            level = HEADING_TYPES[block_type] if block_type == "doc_title" else int(
                block.get("level") or 2
            )
            markdown = _heading_markdown(_sanitize_markdown(content), level)
            return self._text_block(markdown, box, "heading")

        if block_type in TEXT_TYPES:
            if block_type == "page_footnote" and not content.strip():
                return []
            return self._text_block(_sanitize_markdown(content), box, "text")

        if block_type == "table":
            return self._visual_or_fallback(page_index, position, merged, "table", captions, block_type, diagnostics)

        if block_type in {"image", "chart", "figure"}:
            return self._visual_or_fallback(page_index, position, merged, "figure", captions, block_type, diagnostics)

        if block_type in {"code", "algorithm"}:
            text = _strip_fences(content)
            if block_type == "algorithm" or _looks_like_algorithm(content, captions):
                return self._visual_or_fallback(page_index, position, merged, "algorithm", captions, block_type, diagnostics)
            if _looks_like_code(content, merged.get("guess_lang"), merged.get("sub_type")):
                return self._visual_or_fallback(page_index, position, merged, "code", captions, block_type, diagnostics)
            diagnostics.append(
                {
                    "page": page_index,
                    "block": position,
                    "type": block_type,
                    "reason": "MinerU marked this as code but it looks like prose; kept as text",
                    "needs_review": True,
                }
            )
            return self._text_block(_strip_fences(_sanitize_markdown(content)), box, "text")

        # Unknown but textual: keep the content, flag it rather than silently drop.
        if content.strip():
            diagnostics.append(
                {
                    "page": page_index,
                    "block": position,
                    "type": block_type,
                    "reason": "unknown MinerU block type; emitted as text",
                    "needs_review": True,
                }
            )
            return self._text_block(_sanitize_markdown(content), box, "text")
        return []

    def _text_block(self, text: str, box: Optional[List[float]], label: str) -> List[Dict[str, Any]]:
        if not (text or "").strip():
            return []
        self.last_visual = None
        return [
            {
                "label": label,
                "text": text,
                "boxes_norm": [box] if box else [],
            }
        ]

    def _visual_or_fallback(
        self,
        page_index: int,
        position: int,
        block: Dict[str, Any],
        kind: str,
        captions: List[Dict[str, Any]],
        source_type: str,
        diagnostics: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not PRESERVE_VISUAL or (kind not in VISUAL_TYPES and kind != "figure"):
            fallback = str(block.get("content") or "")
            if kind == "table":
                # Tables are HTML we can still render; stripping the tags would
                # destroy the only representation left when imaging is disabled.
                return self._text_block(fallback, block.get("bbox"), "text")
            return self._text_block(
                _sanitize_markdown(fallback), block.get("bbox"), "text"
            )

        # A pseudocode listing can be split across pages and MinerU may label the
        # continuation `code`; keep it in the same visual family so it stacks.
        continued = bool(block.get("continues_prev"))
        if (
            not continued
            and self.last_visual
            and self.last_visual.get("label") == "algorithm"
            and kind == "code"
            and page_index == int(self.last_visual.get("page_index", -99)) + 1
            and _looks_like_pseudocode(str(block.get("content") or ""))
        ):
            continued = True
        if (
            continued
            and self.last_visual
            and self.last_visual.get("label") in {"code", "algorithm"}
            and kind in {"code", "algorithm"}
        ):
            kind = self.last_visual["label"]

        visual = self._make_visual(kind, page_index, block, captions)
        if visual is None:
            diagnostics.append(
                {
                    "page": page_index,
                    "block": position,
                    "type": source_type,
                    "reason": "could not crop this region from the PDF or MinerU images; kept text fallback",
                    "needs_review": True,
                }
            )
            fallback = str(block.get("content") or "")
            if kind in {"code", "algorithm"}:
                fallback = f"```\n{_strip_fences(fallback)}\n```"
            return self._text_block(fallback, block.get("bbox"), "text")

        # Cross-page table/code continuation: stack onto the previous image so
        # the reader sees one table, not two fragments.
        if continued and self.last_visual and self.last_visual["label"] == kind:
            combined = _stack_png([self.last_visual["png"], visual["png"]])
            if combined:
                self._upload_png(self.last_visual["image_key"], combined)
                self.last_visual["png"] = combined
                self.last_visual["search_text"] = (
                    f"{self.last_visual.get('search_text', '')}\n\n{visual.get('search_text', '')}"
                )[:20000]
                logger.info(
                    "stacked cross-page %s on page %s into %s",
                    kind,
                    page_index,
                    self.last_visual["image_key"],
                )
                return self._caption_blocks(captions, block.get("bbox"))
            diagnostics.append(
                {
                    "page": page_index,
                    "block": position,
                    "type": source_type,
                    "reason": "cross-page continuation could not be stacked; kept as a second image",
                    "needs_review": True,
                }
            )

        image_block = {k: v for k, v in visual.items() if k not in {"png", "page_index"}}
        self.last_visual = visual
        emitted: List[Dict[str, Any]] = [image_block]
        emitted.extend(self._caption_blocks(captions, block.get("bbox")))
        return emitted

    def _caption_blocks(
        self, captions: List[Dict[str, Any]], image_box: Optional[List[float]]
    ) -> List[Dict[str, Any]]:
        blocks = []
        for caption in captions:
            if _caption_overlaps(caption.get("bbox"), image_box):
                # The caption is inside the crop; rendering it again would duplicate it.
                continue
            blocks.append(
                {
                    "label": "caption",
                    "text": _sanitize_markdown(caption["text"]),
                    "boxes_norm": [caption["bbox"]] if caption.get("bbox") else [],
                    "search_text": caption["text"],
                }
            )
        return blocks


def build_pages(
    pdf_bytes: bytes,
    paper_id: str,
    document_id: str,
    upload: Any,
    on_stage: Any,
) -> List[Dict[str, Any]]:
    """Run MinerU once and return OpenPaper page/block records."""
    client = MineruClient()
    on_stage("mineru", 0, 1)
    logger.info("submitting paper %s to MinerU (%d bytes)", paper_id, len(pdf_bytes))
    bundle = client.parse(pdf_bytes, f"{paper_id}.pdf")
    on_stage("mineru", 1, 1)

    for name in ("markdown.md", "middle_json.json", "structured_content.json"):
        if not bundle.has(name):
            raise MineruError(
                f"MinerU ZIP is missing {name!r}; cannot rebuild structured Markdown"
            )
    structured = bundle.read_json("structured_content.json")
    middle = bundle.read_json("middle_json.json")

    builder = _Builder(pdf_bytes, paper_id, document_id, bundle, upload)
    pages = builder.build(structured, middle)
    prefix = builder.prefix
    upload(f"{prefix}/source/markdown.md", bundle.read("markdown.md"), "text/markdown")
    upload(
        f"{prefix}/source/middle_json.json",
        bundle.read("middle_json.json"),
        "application/json",
    )
    upload(
        f"{prefix}/source/structured_content.json",
        bundle.read("structured_content.json"),
        "application/json",
    )
    upload(f"{prefix}/content.md", _content_markdown(pages).encode("utf-8"), "text/markdown")
    return pages


def _content_markdown(pages: List[Dict[str, Any]]) -> str:
    """Continuous Markdown for debugging/export; image links are ZIP-relative."""
    parts: List[str] = []
    for page in pages:
        for block in page.get("blocks", []):
            label = block.get("label")
            text = (block.get("text") or "").strip()
            image_key = block.get("image_key")
            if image_key:
                filename = image_key.rsplit("/", 1)[-1]
                alt = {"table": "Table", "algorithm": "Algorithm", "code": "Code"}.get(
                    label or "", "Figure"
                )
                parts.append(f"![{alt}](assets/{filename})")
            elif text:
                parts.append(text)
    return "\n\n".join(parts).strip() + "\n"
