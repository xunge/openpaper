"""Backfill missing paper venues with DeepSeek web search.

Only papers with an empty ``journal`` are candidates. Existing values (manual,
DOI, Zotero, or previous DeepSeek results) are never overwritten.

Usage:
    python -m app.scripts.backfill_venue --limit 50 --dry-run
    python -m app.scripts.backfill_venue --limit 50 --sleep 2

This performs one or more external web-search calls per paper; run it in
bounded batches during off-hours.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from sqlalchemy import or_

from app.database.crud.paper_crud import PaperUpdate, paper_crud
from app.database.database import SessionLocal
from app.database.models import Paper
from app.helpers.deepseek_venue import VenueLookupError, lookup_venue

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _candidates(db, limit):
    query = (
        db.query(Paper)
        .filter(or_(Paper.journal.is_(None), Paper.journal == ""))
        .filter(Paper.title.isnot(None))
        .order_by(Paper.created_at.desc())
    )
    if limit:
        query = query.limit(limit)
    return query.all()


def backfill(limit=None, dry_run=False, sleep=0.0):
    db = SessionLocal()
    try:
        papers = _candidates(db, limit)
        logger.info("Found %d paper(s) missing a venue", len(papers))
        updated = 0
        not_found = 0
        errors = 0

        for index, paper in enumerate(papers, 1):
            title = (paper.title or "")[:100]
            logger.info("[%d/%d] %s  %r", index, len(papers), paper.id, title)
            if dry_run:
                continue

            try:
                result = lookup_venue(
                    title=paper.title,
                    authors=list(paper.authors or []),
                    doi=str(paper.doi) if paper.doi else None,
                )
            except VenueLookupError as exc:
                errors += 1
                logger.warning("  lookup failed: %s", exc)
                continue
            except Exception:
                errors += 1
                logger.exception("  unexpected lookup failure")
                continue

            if not result.venue:
                not_found += 1
                logger.info("  no venue found")
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            provenance = dict(paper.field_provenance or {})
            provenance["journal"] = {
                "source": "deepseek_web_search",
                "model": result.model,
                "filled_at": now_iso,
            }
            updates = {"journal": result.venue, "field_provenance": provenance}
            if result.year and not paper.publish_date:
                updates["publish_date"] = datetime(
                    result.year, 1, 1, tzinfo=timezone.utc
                ).isoformat()
                provenance["publish_date"] = {
                    "source": "deepseek_web_search",
                    "model": result.model,
                    "precision": "year",
                    "filled_at": now_iso,
                }
            try:
                paper_crud.update(
                    db=db, db_obj=paper, obj_in=PaperUpdate(**updates), user=None
                )
                db.commit()
                updated += 1
                logger.info("  -> %s (%s)", result.venue, result.year)
            except Exception:
                db.rollback()
                errors += 1
                logger.exception("  could not save venue")

            if sleep:
                time.sleep(sleep)

        logger.info(
            "Done. updated=%d not_found=%d errors=%d", updated, not_found, errors
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill venues via DeepSeek")
    parser.add_argument("--limit", type=int, default=None, help="max papers")
    parser.add_argument("--dry-run", action="store_true", help="list without calling")
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between papers")
    args = parser.parse_args()
    backfill(limit=args.limit, dry_run=args.dry_run, sleep=args.sleep)
