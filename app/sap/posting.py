"""Post a job's invoice to SAP and record the outcome on the job + audit log."""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import Decision, Job, ReviewStatus
from app.llm.schema import InvoiceFields
from app.routing import RouteResult, record_decision
from app.sap.client import SapError, post_with_retry
from app.sap.payload import PayloadError, build_payload

log = logging.getLogger(__name__)


def effective_fields(job: Job) -> InvoiceFields:
    """Reviewer-corrected fields if present, else the original extraction."""
    return InvoiceFields.model_validate_json(job.corrected_fields_json or job.fields_json)


def post_job(db: Session, job: Job) -> tuple[RouteResult, bool]:
    """Post to SAP. Success: doc number saved + approve audit row. Failure (after retries):
    manual_review with the reason. Never raises; commit is left to the caller."""
    try:
        doc = post_with_retry(build_payload(effective_fields(job)))
    except (SapError, PayloadError) as exc:
        reason = f"SAP posting failed: {exc}"
        log.warning("Job %s: %s", job.id, reason)
        job.sap_error = reason
        result = RouteResult(Decision.MANUAL_REVIEW, [reason], job.llm_confidence)
        record_decision(db, job, result)
        job.review_status = ReviewStatus.PENDING
        return result, False
    job.sap_doc_number = doc
    job.sap_posted_at = datetime.now(UTC)
    job.sap_error = None
    if job.review_status == ReviewStatus.PENDING:
        job.review_status = ReviewStatus.APPROVED
    result = RouteResult(Decision.APPROVE, [f"Posted to SAP: document {doc}"], job.llm_confidence)
    record_decision(db, job, result)
    return result, True
