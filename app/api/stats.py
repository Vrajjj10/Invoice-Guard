"""Dashboard numbers for the UI."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Decision, Job, JobStatus
from app.db.session import get_db
from app.security import require_api_key

router = APIRouter(tags=["stats"], dependencies=[Depends(require_api_key)])


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)) -> dict:
    """Counts over finished jobs.

    flagged = ever sent to manual review; avg time = created -> last update.
    """
    jobs = list(db.scalars(select(Job).where(Job.status == JobStatus.DONE)))
    total = len(jobs)
    approved = sum(j.decision == Decision.APPROVE for j in jobs)
    secs = [(j.updated_at - j.created_at).total_seconds() for j in jobs if j.updated_at]
    return {
        "processed": total,
        "approved": approved,
        "auto_approved_pct": round(100 * approved / total, 1) if total else None,
        "flagged": sum(j.review_status is not None for j in jobs),
        "rejected": sum(j.decision == Decision.REJECT for j in jobs),
        "avg_processing_s": round(sum(secs) / len(secs), 1) if secs else None,
    }
