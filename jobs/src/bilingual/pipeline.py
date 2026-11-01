"""Scanned/digital paper -> per-page blocks, OCR in parallel, translated in order.

This is the execution half of the bilingual reader. It used to run inside the
FastAPI process as a BackgroundTask, which meant a server restart silently killed
the job and every OCR/translation call competed with request handling. It now runs
in the Celery worker; the only way it talks to the server is the callback passed
in by the task wrapper.

All coordinates and image locations are stored as S3 *keys*. The server mints
presigned URLs when it serialises a document, so a stored document does not start
returning 403 image links six hours after it was generated.
"""

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ocr_client, ocr_providers, pdf_layout
from .continuation import merge_continuations, merge_ordered_list_blocks
from .doc_translate import translate_document
from .html_cells import extract_cells, render_cells, split_tables
from .table_normalise import normalise_table
from .table_rebuild import needs_rebuild, rebuild_table
from .text_normalise import align_ordered_list_markers, normalise_math

logger = logging.getLogger(__name__)

OCR_WORKERS = int(os.getenv("OCR_WORKERS", "4"))

_OPEN_TABLE = re.compile(r"<table\b", re.IGNORECASE)
_CLOSE_TABLE = re.compile(r"</table\s*>", re.IGNORECASE)

Upload = Callable[[str, bytes, str], None]
StageCallback = Callable[[str, int, int], None]
PageCallback = Callable[[Dict[str, Any]], None]


def ensure_closed_tables(text: str) -> str:
    """Close a table the OCR truncated.

    DeepSeek-OCR sometimes stops before `</table>`. Without a closing tag the
    block is not recognised as a table at all: it is sent to the translator as one
    huge paragraph, and the frontend refuses to render it as HTML. Appending the
    missing closers is enough for both the cell extractor and the browser.
    """
    if not text:
        return text
    missing = len(_OPEN_TABLE.findall(text)) - len(_CLOSE_TABLE.findall(text))
    return text + "\n</table>" * missing if missing > 0 else text


def _table_steps(
    text: str, page_index: int, block_index: int, jobs: List[Tuple[str, str]]
) -> List[Dict[str, Any]]:
    """Turn one table block into translatable steps, falling back gracefully.

    A table whose markup is too broken for cell extraction is translated as a
    whole (and, if that fails too, kept in the source language). Returning the
    original text is the important part: a missing translation used to render as
    an empty block, which looked like the page had been dropped.
    """
    steps: List[Dict[str, Any]] = []
    for kind, payload in split_tables(text):
        if kind == "text":
            if payload.strip():
                job_id = f"{page_index}-{block_index}-t{len(steps)}"
                steps.append({"kind": "job", "id": job_id, "source": payload})
                jobs.append((job_id, payload))
            else:
                steps.append({"kind": "raw", "text": payload})
            continue

        template, cells = extract_cells(payload)
        if cells:
            cell_steps = []
            for cell_index, cell in enumerate(cells):
                job_id = f"{page_index}-{block_index}-c{len(steps)}-{cell_index}"
                cell_steps.append({"id": job_id, "source": cell})
                jobs.append((job_id, cell))
            steps.append({"kind": "table", "template": template, "cells": cell_steps})
            continue

        # No <td>/<th> to swap: either the cells are glued together or the model
        # emitted the content without markup. Ask for a markdown grid, then
        # translate that as ordinary text.
        rebuilt = rebuild_table(payload) if needs_rebuild(payload) else None
        if rebuilt:
            job_id = f"{page_index}-{block_index}-r{len(steps)}"
            steps.append({"kind": "job", "id": job_id, "source": rebuilt})
            jobs.append((job_id, rebuilt))
        else:
            job_id = f"{page_index}-{block_index}-x{len(steps)}"
            steps.append({"kind": "job", "id": job_id, "source": payload})
            jobs.append((job_id, payload))

    return steps


def _assemble(
    page: Dict[str, Any], steps: List[Dict[str, Any]], translations: Dict[str, str]
) -> str:
    """Render the Chinese text for one page from its translated steps."""
    pieces: List[str] = []
    for step in steps:
        if step["kind"] == "job":
            pieces.append(translations.get(step["id"], step["source"]))
            continue
        if step["kind"] == "raw":
            pieces.append(step["text"])
            continue

        template = step["template"]
        rendered_cells = [
            translations.get(cell["id"], cell["source"]) for cell in step["cells"]
        ]
        pieces.append(normalise_table(render_cells(template, rendered_cells)))
    # The translator can drop a closing tag; the frontend only renders HTML when
    # it sees `</table>`, otherwise the raw markup leaks into the reader.
    return ensure_closed_tables(normalise_math("".join(pieces)))


def _build_ocr_pages(
    pdf_bytes: bytes,
    paper_id: str,
    provider_name: str,
    upload: Upload,
    on_stage: StageCallback,
) -> Tuple[List[Dict[str, Any]], int]:
    """Per-page OCR path used by DeepSeek/Xiaomi (and any future page model)."""
    on_stage("render", 0, 0)
    pages = ocr_client.render_page_images(pdf_bytes)
    total = len(pages)
    if total == 0:
        raise ValueError("PDF has no renderable pages")

    for index, png_bytes, _w, _h in pages:
        upload(
            f"uploads/{paper_id}-bilingual-page-{index}.png",
            png_bytes,
            "image/png",
        )
    on_stage("render", total, total)

    # --- OCR all pages concurrently -------------------------------------
    def run_ocr(page: Tuple[int, bytes, int, int]):
        index, png_bytes, _w, _h = page
        try:
            blocks, raw, degenerate = ocr_client.ocr_page_with(provider_name, png_bytes)
            return index, {"blocks": blocks, "raw": raw, "degenerate": degenerate}
        except Exception as exc:  # one bad page must not sink the document
            logger.warning("OCR failed for page %s: %s", index, exc)
            return index, {"blocks": [], "raw": "", "degenerate": True}

    ocr_results: Dict[int, Dict[str, Any]] = {}
    completed = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=OCR_WORKERS) as pool:
        futures = [pool.submit(run_ocr, page) for page in pages]
        for future in as_completed(futures):
            index, result = future.result()
            ocr_results[index] = result
            with lock:
                completed += 1
                on_stage("ocr", completed, total)
    logger.info("OCR finished for %d page(s)", len(ocr_results))

    # --- prepare blocks per page ---------------------------------------
    on_stage("prepare", 0, total)
    prepared_pages: List[Dict[str, Any]] = []
    for index, png_bytes, _w, _h in pages:
        page_key = f"uploads/{paper_id}-bilingual-page-{index}.png"
        page_w, page_h = pdf_layout.page_size(pdf_bytes, index)
        pdf_blocks = pdf_layout.text_blocks(pdf_bytes, index)
        figures = pdf_layout.figure_rects(pdf_bytes, index)
        figure_cursor = 0

        prepared: List[Dict] = []
        page_ocr = ocr_results.get(index, {"blocks": [], "raw": "", "degenerate": False})
        for block in page_ocr["blocks"]:
            text = normalise_math(block["text"])
            if block["label"] in ocr_client.FIGURE_LABELS:
                image_key = None
                box = (block.get("boxes") or [None])[0]
                if box:
                    # Model boxes are normalised to 0-999 and map straight onto
                    # the rendered page image.
                    norm = [round(v / ocr_client.BOX_SCALE, 5) for v in box]
                    cropped = ocr_client.crop_region_norm(png_bytes, norm)
                elif figure_cursor < len(figures):
                    rect = figures[figure_cursor]
                    figure_cursor += 1
                    cropped = ocr_client.crop_region_pdf(pdf_bytes, index, rect)
                else:
                    cropped = None
                if cropped:
                    image_key = f"{page_key}-figure-{block.get('index', figure_cursor)}"
                    upload(image_key, cropped, "image/png")
                prepared.append(
                    {
                        "label": block["label"],
                        "text": "",
                        "boxes_norm": (
                            [[round(v / ocr_client.BOX_SCALE, 5) for v in box]]
                            if box
                            else []
                        ),
                        "image_key": image_key,
                    }
                )
                continue
            if not text.strip():
                continue
            prepared.append(
                {
                    "label": block["label"],
                    "text": text,
                    "boxes_norm": [],
                    "boxes": block.get("boxes") or [],
                    "image_key": None,
                }
            )

        # Prefer the model's own boxes (0-999) — they are exact for scans too.
        for block in prepared:
            box = (block.get("boxes") or [None])[0]
            if box:
                block["boxes_norm"] = [[round(v / ocr_client.BOX_SCALE, 5) for v in box]]

        text_indices = [
            i for i, b in enumerate(prepared) if b["text"].strip() and not b["boxes_norm"]
        ]
        if text_indices:
            rects = pdf_layout.match_blocks_ordered(
                [prepared[i]["text"] for i in text_indices], pdf_blocks
            )
            for slot, rect in zip(text_indices, rects):
                if rect:
                    prepared[slot]["boxes_norm"] = [
                        pdf_layout.normalise(rect, (page_w, page_h))
                    ]

        # --- recover figures the OCR backend did not mark -------------------
        # Xiaomi returns plain markdown with no image blocks, so its reader would
        # never show a figure inline. Detect figures from the PDF itself and
        # insert them at the matching reading position. Dedupe against figures
        # the OCR already found and against tables, which are rendered as HTML.
        existing_figures = [
            b["boxes_norm"][0]
            for b in prepared
            if b.get("image_key") and b.get("boxes_norm")
        ]
        table_boxes = [
            box
            for b in prepared
            if "<table" in (b.get("text") or "").lower()
            for box in (b.get("boxes_norm") or [])
        ]
        for figure_index, rect in enumerate(pdf_layout.figure_regions(pdf_bytes, index)):
            norm = pdf_layout.normalise(rect, (page_w, page_h))
            if any(
                pdf_layout.overlap_ratio(norm, box) > 0.35 for box in existing_figures
            ):
                continue
            if any(pdf_layout.overlap_ratio(norm, box) > 0.30 for box in table_boxes):
                continue
            cropped = ocr_client.crop_region_pdf(pdf_bytes, index, rect)
            if not cropped:
                continue
            image_key = f"{page_key}-pdf-figure-{figure_index}"
            upload(image_key, cropped, "image/png")
            block = {
                "label": "image",
                "text": "",
                "boxes_norm": [norm],
                "image_key": image_key,
            }
            insert_at = len(prepared)
            for position, candidate in enumerate(prepared):
                boxes = candidate.get("boxes_norm") or []
                if boxes and boxes[0][1] > norm[1]:
                    insert_at = position
                    break
            prepared.insert(insert_at, block)

        prepared_pages.append(
            {
                "index": index,
                "image_key": page_key,
                "blocks": prepared,
                "raw": page_ocr.get("raw", ""),
                "degenerate": page_ocr.get("degenerate", False),
            }
        )
    return prepared_pages, total


def _finalize_pages(
    pdf_bytes: bytes,
    prepared_pages: List[Dict[str, Any]],
    total: int,
    on_stage: StageCallback,
    on_page: Optional[PageCallback] = None,
) -> List[Dict[str, Any]]:
    """Merge page-split paragraphs, translate, then assemble the reader pages."""
    hyphenated_blocks = pdf_layout.hyphenated_block_ids(pdf_bytes, prepared_pages)
    merged = merge_continuations(
        prepared_pages, hyphenated_blocks=hyphenated_blocks
    )
    if merged:
        logger.info(
            "merged %d page/column opening text block(s) into previous paragraphs",
            merged,
        )

    lists_merged = merge_ordered_list_blocks(prepared_pages)
    if lists_merged:
        logger.info("merged %d ordered list item(s) into continuous lists", lists_merged)
    on_stage("prepare", total, total)
    # --- flatten in reading order and translate in bounded chunks --------
    jobs: List[Tuple[str, str]] = []
    plan: List[List[Dict[str, Any]]] = []
    for page in prepared_pages:
        page_plan: List[Dict[str, Any]] = []
        for block_index, block in enumerate(page["blocks"]):
            text = ensure_closed_tables(block["text"])
            if not text.strip():
                page_plan.append({"kind": "empty"})
                continue
            if "<table" not in text.lower():
                job_id = f"{page['index']}-{block_index}"
                page_plan.append({"kind": "job", "id": job_id, "source": text})
                jobs.append((job_id, text))
                continue
            page_plan.append(
                {
                    "kind": "steps",
                    "steps": _table_steps(text, page["index"], block_index, jobs),
                }
            )
        plan.append(page_plan)

    translations = translate_document(
        jobs, on_progress=lambda done, total: on_stage("translate", done, total)
    )

    # --- assemble and hand pages over as they finish ---------------------
    results: List[Dict[str, Any]] = []
    for page, page_plan in zip(prepared_pages, plan):
        rendered_blocks = []
        for block_index, (block, entry) in enumerate(zip(page["blocks"], page_plan)):
            if entry["kind"] == "empty":
                zh = ""
            elif entry["kind"] == "job":
                zh = translations.get(entry["id"], entry["source"])
            else:
                zh = _assemble(block, entry["steps"], translations)
            if block["text"].strip() and zh:
                zh = align_ordered_list_markers(block["text"], zh)
            rendered_blocks.append(
                {
                    "id": f"{page['index']}-{block_index}",
                    "label": block["label"],
                    "boxes_norm": block["boxes_norm"],
                    "en_md": ensure_closed_tables(block["text"]),
                    "zh_md": (
                        ensure_closed_tables(normalise_math(zh))
                        if block["text"]
                        else ""
                    ),
                    "image_key": block.get("image_key"),
                    # Kept out of the reading Markdown but exposed to the API
                    # for search/RAG and review of image-ified regions.
                    "search_text": block.get("search_text", ""),
                    "needs_review": block.get("needs_review", False),
                }
            )

        result = {
            "index": page["index"],
            "image_key": page["image_key"],
            "zh_md": "\n\n".join(b["zh_md"] for b in rendered_blocks if b["zh_md"]),
            "en_md": "\n\n".join(b["en_md"] for b in rendered_blocks if b["en_md"]),
            "raw_ocr": page.get("raw", ""),
            "ocr_degenerate": page.get("degenerate", False),
            "diagnostics": page.get("diagnostics", []),
            "search_text": page.get("search_text", ""),
            "blocks": rendered_blocks,
        }
        results.append(result)
        if on_page:
            on_page(result)
        on_stage("assemble", len(results), total)

    return results


def generate_pages(
    pdf_bytes: bytes,
    paper_id: str,
    provider: Optional[str],
    upload: Upload,
    on_stage: StageCallback,
    on_page: Optional[PageCallback] = None,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run the whole pipeline and return the page records to persist.

    `on_stage(stage, done, total)` is called often enough to act as a heartbeat;
    `on_page` receives each finished page so the reader can show pages as they
    become available instead of after the entire document.

    MinerU is document-level: one HTTP call returns structured blocks for every
    page. It builds the same page/block shape as the per-page OCR providers, so
    translation, cross-page merging and callbacks stay a single shared flow.
    """
    provider_name = ocr_providers.resolve(provider)["name"]

    if provider_name == "mineru":
        from . import mineru_layout

        prepared_pages = mineru_layout.build_pages(
            pdf_bytes,
            paper_id=paper_id,
            document_id=document_id or paper_id,
            upload=upload,
            on_stage=on_stage,
        )
        total = len(prepared_pages)
    else:
        prepared_pages, total = _build_ocr_pages(
            pdf_bytes,
            paper_id=paper_id,
            provider_name=provider_name,
            upload=upload,
            on_stage=on_stage,
        )

    return _finalize_pages(
        pdf_bytes,
        prepared_pages=prepared_pages,
        total=total,
        on_stage=on_stage,
        on_page=on_page,
    )
