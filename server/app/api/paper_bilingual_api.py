"""Bilingual (scanned PDF -> Chinese markdown) document endpoints.

The OCR + translation work runs in the Celery worker (``generate_bilingual_document``);
this module creates the document, queues the task, serves status/document reads and
accepts the worker's progress webhooks. Keeping the work out of the API process
means a server restart no longer strands a run at "running" forever.

A run that has not reported progress for ``BILINGUAL_STALE_SECONDS`` is treated as
dead and can be started again, which is the recovery path for a worker that was
restarted mid-document.
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.database.models import PaperDocument
from app.helpers import ocr_probe
from app.helpers.bilingual_jobs import revoke_bilingual_task, submit_bilingual_task
from app.helpers.s3 import s3_service
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

paper_bilingual_router = APIRouter()

ACTIVE_STATES = {"pending", "running"}
STALE_SECONDS = int(os.getenv("BILINGUAL_STALE_SECONDS", "420"))


def _presigned(key: str | None) -> str | None:
    if not key:
        return None
    return s3_service.generate_presigned_url(object_key=key, expiration=6 * 3600)


def _serialise(document: PaperDocument, presign: bool = True) -> dict:
    pages = []
    for page in document.pages or []:
        blocks = []
        for block in page.get("blocks", []):
            item = dict(block)
            image_key = item.pop("image_key", None)
            # Documents written before the Celery move stored a presigned URL
            # instead of a key; keep showing them instead of blanking the figure.
            item["image_url"] = (
                _presigned(image_key) if (presign and image_key) else item.get("image_url")
            )
            blocks.append(item)
        pages.append(
            {
                "index": page.get("index"),
                "image_url": (
                    _presigned(page.get("image_key"))
                    if (presign and page.get("image_key"))
                    else page.get("image_url")
                ),
                "zh_md": page.get("zh_md", ""),
                "en_md": page.get("en_md", ""),
                "raw_ocr": page.get("raw_ocr", ""),
                "ocr_degenerate": page.get("ocr_degenerate", False),
                "search_text": page.get("search_text", ""),
                "diagnostics": page.get("diagnostics", []),
                "blocks": blocks,
            }
        )
    content_key = None
    source_urls = None
    if document.ocr_model == "mineru":
        prefix = f"papers/{document.paper_id}/ocr/{document.id}"
        content_key = f"{prefix}/content.md"
        if presign:
            source_urls = {
                "markdown": _presigned(f"{prefix}/source/markdown.md"),
                "middle_json": _presigned(f"{prefix}/source/middle_json.json"),
                "structured_content": _presigned(
                    f"{prefix}/source/structured_content.json"
                ),
            }
    return {
        "id": str(document.id),
        "paper_id": str(document.paper_id),
        "status": document.status,
        "progress": document.progress,
        "page_count": document.page_count,
        "stage": document.stage,
        "stage_progress": document.stage_progress,
        "stage_total": document.stage_total,
        "error": document.error,
        "ocr_model": document.ocr_model,
        "translation_model": document.translation_model,
        "content_url": _presigned(content_key) if presign else None,
        "source_urls": source_urls,
        "pages": pages,
    }


def _is_stale(document: PaperDocument) -> bool:
    if document.status not in ACTIVE_STATES:
        return False
    updated = document.updated_at or document.created_at
    if updated is None:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated > timedelta(seconds=STALE_SECONDS)


def _expire(db: Session, document: PaperDocument) -> None:
    """Mark a silent run as failed so the reader offers a retry instead of a spinner."""
    logger.warning(
        "expiring stale bilingual document %s (task %s, stage %s, %ss without progress)",
        document.id,
        document.task_id,
        document.stage,
        STALE_SECONDS,
    )
    document.status = "failed"
    document.error = (
        "任务已超时（worker 可能已重启或队列阻塞），请重新生成。"
        "The OCR job stopped reporting progress; generate it again."
    )
    document.stage = "done"
    db.add(document)
    db.commit()
    db.refresh(document)


def _find(db: Session, paper_id: uuid.UUID, user_id) -> PaperDocument | None:
    return (
        db.query(PaperDocument)
        .filter(
            PaperDocument.paper_id == paper_id,
            PaperDocument.user_id == user_id,
        )
        .first()
    )


@paper_bilingual_router.get("/")
async def get_bilingual_document(
    request: Request,
    id: str = Query(..., description="Paper id"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """Return the stored document, or a null document when none exists yet."""
    try:
        paper_id = uuid.UUID(id)
    except ValueError:
        return JSONResponse(status_code=400, content={"message": "Invalid paper id"})

    document = _find(db, paper_id, current_user.id)
    if not document:
        return JSONResponse(
            status_code=200,
            content={"document": None, "ocrAvailable": ocr_probe.ocr_available()},
        )
    if _is_stale(document):
        _expire(db, document)
    return JSONResponse(
        status_code=200,
        content={
            "document": _serialise(document),
            "ocrAvailable": ocr_probe.ocr_available(),
        },
    )


@paper_bilingual_router.get("/models")
async def list_ocr_models(
    request: Request,
    current_user: CurrentUser = Depends(get_required_user),
):
    """OCR backends the user can choose from, with live availability."""
    return JSONResponse(
        status_code=200,
        content={
            "models": ocr_probe.available_providers(),
            "default": ocr_probe.DEFAULT_PROVIDER,
        },
    )


@paper_bilingual_router.post("/")
async def start_bilingual_document(
    request: Request,
    id: str = Query(..., description="Paper id"),
    model: str | None = Query(None, description="OCR backend name"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """Kick off OCR + translation for a paper (idempotent while a run is alive)."""
    try:
        paper_id = uuid.UUID(id)
    except ValueError:
        return JSONResponse(status_code=400, content={"message": "Invalid paper id"})

    paper = paper_crud.get(db, id=id, user=current_user)
    if not paper or not paper.s3_object_key:
        return JSONResponse(status_code=404, content={"message": "Paper not found"})

    provider = ocr_probe.resolve(model)
    provider_ready = any(
        item["name"] == provider["name"] and item["available"]
        for item in ocr_probe.available_providers()
    )
    if not provider_ready:
        return JSONResponse(
            status_code=503,
            content={"message": f"OCR backend {provider['label']} is not available"},
        )

    document = _find(db, paper_id, current_user.id)

    if document and document.status in ACTIVE_STATES:
        if not _is_stale(document):
            return JSONResponse(
                status_code=202, content={"document": _serialise(document)}
            )
        # A previous worker died mid-run. Its task id is dead too; clear it before
        # the new run so late events from the old task cannot overwrite this one.
        document.task_id = None
        document.status = "failed"
        document.error = None

    if not document:
        document = PaperDocument(
            paper_id=paper_id,
            user_id=current_user.id,
            status="pending",
            progress=0,
            stage="pending",
            stage_progress=0,
            stage_total=0,
        )
        db.add(document)

    # Regenerate from scratch: stale pages would otherwise mix with new ones.
    document.status = "pending"
    document.progress = 0
    document.page_count = None
    document.stage = "pending"
    document.stage_progress = 0
    document.stage_total = 0
    document.error = None
    document.pages = []
    document.ocr_model = provider["name"]
    document.translation_model = os.getenv(
        "TRANSLATE_MODEL", os.getenv("DEEPSEEK_FAST_MODEL", "deepseek-flash")
    )
    document.task_id = None
    db.add(document)
    db.commit()
    db.refresh(document)

    try:
        task_id = submit_bilingual_task(
            document_id=str(document.id),
            paper_id=str(paper.id),
            s3_object_key=str(paper.s3_object_key),
            provider=provider["name"],
        )
    except Exception as exc:
        logger.exception("could not queue bilingual document %s", document.id)
        document.status = "failed"
        document.error = f"无法提交 OCR 任务：{exc}"[:500]
        db.add(document)
        db.commit()
        db.refresh(document)
        return JSONResponse(
            status_code=502,
            content={
                "message": "Could not queue the OCR job",
                "document": _serialise(document),
            },
        )

    document.task_id = task_id
    db.add(document)
    db.commit()
    db.refresh(document)
    logger.info(
        "queued bilingual document %s as task %s (%s)",
        document.id,
        task_id,
        provider["name"],
    )
    return JSONResponse(status_code=202, content={"document": _serialise(document)})


@paper_bilingual_router.delete("/")
async def delete_bilingual_document(
    request: Request,
    id: str = Query(..., description="Paper id"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    try:
        paper_id = uuid.UUID(id)
    except ValueError:
        return JSONResponse(status_code=400, content={"message": "Invalid paper id"})

    document = _find(db, paper_id, current_user.id)
    if not document:
        return JSONResponse(status_code=204, content=None)

    revoke_bilingual_task(document.task_id)
    for page in document.pages or []:
        if page.get("image_key"):
            s3_service.delete_file(page["image_key"])
        for block in page.get("blocks", []):
            if block.get("image_key"):
                s3_service.delete_file(block["image_key"])
    db.delete(document)
    db.commit()
    return JSONResponse(status_code=200, content={"message": "deleted"})
