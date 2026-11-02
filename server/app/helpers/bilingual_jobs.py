"""Submit and revoke bilingual OCR jobs on the Celery worker.

The server keeps the Celery *client* only: the worker owns the pipeline. Queue
name, task name and callback path are the contract between the two services; the
worker's ``start_worker.sh`` must consume the ``bilingual`` queue.
"""

import logging
import uuid
from typing import Optional

from celery import Celery
from app.helpers.celery_config import get_celery_broker_url, get_webhook_base_url

logger = logging.getLogger(__name__)

_app: Optional[Celery] = None


def _client() -> Celery:
    global _app
    if _app is None:
        _app = Celery("openpaper_bilingual", broker=get_celery_broker_url())
        _app.conf.update(
            broker_connection_retry_on_startup=True,
            broker_connection_retry=True,
            broker_connection_max_retries=3,
            task_serializer="json",
            accept_content=["json"],
        )
    return _app


def submit_bilingual_task(
    document_id: str,
    paper_id: str,
    s3_object_key: str,
) -> str:
    """Queue a document for OCR + translation; returns the Celery task id."""
    task_id = str(uuid.uuid4())
    webhook_url = f"{get_webhook_base_url()}/api/webhooks/bilingual/{document_id}"
    _client().send_task(
        "generate_bilingual_document",
        kwargs={
            "document_id": document_id,
            "paper_id": paper_id,
            "s3_object_key": s3_object_key,
            "webhook_url": webhook_url,
        },
        task_id=task_id,
        # The server's Celery instance has no task_routes, so pin the queue here.
        queue="bilingual",
    )
    return task_id


def revoke_bilingual_task(task_id: Optional[str]) -> None:
    """Best-effort cancel. Never let a missing broker block a delete."""
    if not task_id:
        return
    try:
        _client().control.revoke(task_id, terminate=False)
        logger.info("revoked bilingual task %s", task_id)
    except Exception as exc:  # pragma: no cover - broker may be down
        logger.warning("could not revoke bilingual task %s: %s", task_id, exc)
