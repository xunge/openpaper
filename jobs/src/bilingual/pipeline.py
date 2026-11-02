"""Scanned/digital paper -> MinerU pages/blocks -> translated reading pages.

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
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import pdf_layout
from .continuation import merge_continuations, merge_ordered_list_blocks
from .doc_translate import translate_document
from .text_normalise import align_ordered_list_markers, normalise_math

logger = logging.getLogger(__name__)

Upload = Callable[[str, bytes, str], None]
StageCallback = Callable[[str, int, int], None]
PageCallback = Callable[[Dict[str, Any]], None]


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
            text = block["text"]
            if not text.strip():
                page_plan.append({"kind": "empty"})
                continue
            job_id = f"{page['index']}-{block_index}"
            page_plan.append({"kind": "job", "id": job_id, "source": text})
            jobs.append((job_id, text))
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
            else:
                zh = translations.get(entry["id"], entry["source"])
            if block["text"].strip() and zh:
                zh = align_ordered_list_markers(block["text"], zh)
            rendered_blocks.append(
                {
                    "id": f"{page['index']}-{block_index}",
                    "label": block["label"],
                    "boxes_norm": block["boxes_norm"],
                    "en_md": block["text"],
                    "zh_md": normalise_math(zh) if block["text"] else "",
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
    page. It builds the same page/block shape used by translation, cross-page
    merging and callbacks.
    """
    from . import mineru_layout

    prepared_pages = mineru_layout.build_pages(
        pdf_bytes,
        paper_id=paper_id,
        document_id=document_id or paper_id,
        upload=upload,
        on_stage=on_stage,
    )
    total = len(prepared_pages)

    return _finalize_pages(
        pdf_bytes,
        prepared_pages=prepared_pages,
        total=total,
        on_stage=on_stage,
        on_page=on_page,
    )
