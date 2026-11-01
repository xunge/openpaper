"""Download the final OCR reading result as Markdown, ZIP or DOCX."""

import logging
import uuid

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.database.models import PaperDocument
from app.helpers.paper_export import (
    DOCX_MIME,
    ExportError,
    build_markdown,
    build_zip,
    collect_assets,
    content_disposition,
    get_or_build_docx,
)
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

paper_export_router = APIRouter()

_FORMATS = {"md", "zip", "docx"}
_MIME = {
    "md": "text/markdown; charset=utf-8",
    "zip": "application/zip",
    "docx": DOCX_MIME,
}
_EXT = {"md": ".md", "zip": ".zip", "docx": ".docx"}


@paper_export_router.get("/{paper_id}/export")
def export_paper(
    paper_id: str,
    fmt: str = Query("md", alias="format", description="md | zip | docx"),
    lang: str = Query("zh", description="zh | en"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """Export the final bilingual OCR blocks.

    The Markdown is rebuilt from `paper_documents.pages[].blocks[]`, the same
    data the reader displays; MinerU's raw markdown.md is not used.
    """
    fmt = (fmt or "md").strip().lower()
    lang = (lang or "zh").strip().lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, "format must be md, zip or docx")
    if lang not in {"zh", "en"}:
        raise HTTPException(400, "lang must be zh or en")
    try:
        paper_uuid = uuid.UUID(paper_id)
    except ValueError:
        raise HTTPException(400, "Invalid paper id")

    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise HTTPException(404, "Paper not found")

    document = (
        db.query(PaperDocument)
        .filter(
            PaperDocument.paper_id == paper_uuid,
            PaperDocument.user_id == current_user.id,
            PaperDocument.status == "completed",
        )
        .order_by(PaperDocument.updated_at.desc())
        .first()
    )
    if not document:
        raise HTTPException(404, "This paper has no completed OCR document to export")

    title = str(paper.title or f"paper-{paper_id[:8]}")
    try:
        if fmt == "docx":
            markdown, assets = build_markdown(document, lang, image_attrs=True)
            data = get_or_build_docx(document, lang, markdown, assets)
        else:
            markdown, assets = build_markdown(document, lang, image_attrs=False)
            if fmt == "zip":
                data = build_zip(markdown, collect_assets(assets))
            else:
                data = markdown.encode("utf-8")
    except ExportError as exc:
        logger.warning("export failed for paper %s: %s", paper_id, exc.detail)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    headers = {
        "Content-Disposition": content_disposition(title, _EXT[fmt]),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=data, media_type=_MIME[fmt], headers=headers)
