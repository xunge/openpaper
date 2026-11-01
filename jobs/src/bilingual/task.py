"""Celery entry point for the bilingual (OCR + translation) pipeline.

The server owns the database; this task only reads the PDF from S3, does the work
and reports progress back over HTTP. That keeps the worker free of SQLAlchemy and
lets a long OCR run survive an API deploy.

Every callback carries the Celery task id. The server uses it to ignore events
from a superseded run (the reader can regenerate a document while an old task is
still finishing).
"""

import logging
import os
import time
from typing import Any, Dict, List

import requests
from celery.exceptions import SoftTimeLimitExceeded

from src.bilingual.pipeline import generate_pages
from src.celery_app import celery_app
from src.s3_service import s3_service

logger = logging.getLogger(__name__)


class BilingualCallback:
    """Small retrying client for the server's bilingual webhook."""

    def __init__(self, url: str, secret: str, task_id: str) -> None:
        self.url = url
        self.secret = secret
        self.task_id = task_id
        self.headers = {"Content-Type": "application/json"}
        if secret:
            self.headers["X-Internal-Secret"] = secret
        self.page_count = 0

    def _post(self, payload: Dict[str, Any], attempts: int = 1) -> bool:
        body = {"task_id": self.task_id, **payload}
        for attempt in range(1, attempts + 1):
            try:
                response = requests.post(
                    self.url, json=body, headers=self.headers, timeout=60
                )
                response.raise_for_status()
                return True
            except requests.RequestException as exc:
                logger.warning(
                    "bilingual webhook failed (%s/%s): %s", attempt, attempts, exc
                )
                if attempt < attempts:
                    time.sleep(min(10, 2 ** attempt))
        return False

    def stage(self, stage: str, done: int, total: int) -> None:
        if stage == "render" and total:
            self.page_count = total
        self._post(
            {
                "event": "progress",
                "status": "running",
                "stage": stage,
                "stage_progress": done,
                "stage_total": total,
                "page_count": self.page_count or None,
            }
        )

    def page(self, page: Dict[str, Any], total: int = 0) -> None:
        total = self.page_count or total
        self._post(
            {
                "event": "progress",
                "status": "running",
                "stage": "assemble",
                "stage_progress": page["index"] + 1,
                "stage_total": total,
                "page_count": total,
                "pages": [page],
            }
        )

    def completed(self, pages: List[Dict[str, Any]]) -> None:
        self._post(
            {
                "event": "completed",
                "status": "completed",
                "stage": "done",
                "stage_progress": len(pages),
                "stage_total": len(pages),
                "progress": len(pages),
                "page_count": len(pages),
                "pages": pages,
            },
            attempts=3,
        )

    def failed(self, error: str) -> None:
        self._post(
            {
                "event": "failed",
                "status": "failed",
                "error": (error or "bilingual pipeline failed")[:500],
            },
            attempts=3,
        )


@celery_app.task(
    bind=True,
    name="generate_bilingual_document",
    soft_time_limit=3600,
    time_limit=3900,
)
def generate_bilingual_document(
    self,
    document_id: str,
    paper_id: str,
    s3_object_key: str,
    webhook_url: str,
    provider: str | None = None,
) -> Dict[str, Any]:
    """OCR and translate one paper, reporting progress to the server webhook."""
    task_id = str(self.request.id)
    # The secret stays in the worker's own environment; putting it in task
    # kwargs would expose it in broker messages and in Flower.
    callback = BilingualCallback(
        webhook_url, os.getenv("JOBS_INTERNAL_SECRET", ""), task_id
    )
    logger.info(
        "bilingual task %s starting (document=%s paper=%s provider=%s)",
        task_id,
        document_id,
        paper_id,
        provider,
    )

    def upload(object_key: str, data: bytes, content_type: str) -> None:
        s3_service.upload_bytes(object_key, data, content_type)

    try:
        pdf_bytes = s3_service.download_file_to_bytes(s3_object_key)
        pages = generate_pages(
            pdf_bytes,
            paper_id=paper_id,
            provider=provider,
            upload=upload,
            on_stage=callback.stage,
            on_page=callback.page,
            document_id=document_id,
        )
        callback.completed(pages)
        logger.info(
            "bilingual task %s completed (%d pages)", task_id, len(pages)
        )
        return {"document_id": document_id, "status": "completed", "pages": len(pages)}
    except SoftTimeLimitExceeded:
        logger.error("bilingual task %s hit the soft time limit", task_id)
        callback.failed("OCR/翻译超时，请重试或改用较快的 OCR 模型")
        raise
    except Exception as exc:
        logger.exception("bilingual task %s failed", task_id)
        callback.failed(str(exc) or repr(exc))
        raise
