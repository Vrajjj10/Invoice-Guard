"""Review queue: list, inspect, approve (posts to SAP), correct, reject."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.loop import REQUIRED_CALLS, tool_key
from app.agent.tools import ToolContext, run_tool
from app.db.models import AuditLog, Decision, Job, ReviewStatus
from app.db.session import get_db
from app.llm.schema import InvoiceFields
from app.routing import route
from app.sap.posting import effective_fields, post_job
from app.validators import duplicate_hash

router = APIRouter(prefix="/reviews", tags=["reviews"])


class CorrectBody(BaseModel):
    fields: dict  # partial InvoiceFields; merged over the current values


class RejectBody(BaseModel):
    reason: str = ""


def _get_pending(db: Session, job_id: str) -> Job:
    job = _get_job(db, job_id)
    if job.review_status != ReviewStatus.PENDING:
        raise HTTPException(409, f"Review is {job.review_status}, not pending")
    return job


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.review_status is None:
        raise HTTPException(404, "Review not found")
    return job


def _audit(db: Session, job: Job, decision: str, reasons: list[str]) -> None:
    db.add(AuditLog(job_id=job.id, decision=decision, reasons=json.dumps(reasons),
                    confidence=job.llm_confidence, model=job.llm_model))


def _reasons(db: Session, job_id: str) -> list[str]:
    row = db.scalars(
        select(AuditLog)
        .where(AuditLog.job_id == job_id, AuditLog.decision == Decision.MANUAL_REVIEW)
        .order_by(AuditLog.id.desc())
    ).first()
    return json.loads(row.reasons) if row else []


@router.get("")
def list_reviews(
    status: Literal["pending", "approved", "rejected"] | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Review queue, newest first; optional status filter."""
    q = select(Job).where(Job.review_status.is_not(None)).order_by(Job.created_at.desc())
    if status:
        q = q.where(Job.review_status == status)
    out = []
    for j in db.scalars(q):
        f = json.loads(j.corrected_fields_json or j.fields_json or "{}")
        out.append({"id": j.id, "filename": j.filename, "review_status": j.review_status,
                    "decision": j.decision, "vendor_name": f.get("vendor_name"),
                    "invoice_number": f.get("invoice_number"),
                    "grand_total": f.get("grand_total"), "confidence": j.llm_confidence,
                    "sap_doc_number": j.sap_doc_number, "created_at": j.created_at})
    return out


@router.get("/{job_id}")
def get_review(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Extracted data, reasons and the full audit trail."""
    job = _get_job(db, job_id)
    trail = db.scalars(select(AuditLog).where(AuditLog.job_id == job.id).order_by(AuditLog.id))
    return {
        "id": job.id,
        "filename": job.filename,
        "review_status": job.review_status,
        "decision": job.decision,
        "fields": json.loads(job.fields_json) if job.fields_json else None,
        "corrected_fields": json.loads(job.corrected_fields_json)
        if job.corrected_fields_json else None,
        "corrections": json.loads(job.corrections_json) if job.corrections_json else [],
        "reasons": _reasons(db, job.id),
        "checks": json.loads(job.checks_json) if job.checks_json else None,
        "sap_doc_number": job.sap_doc_number,
        "sap_error": job.sap_error,
        "audit_trail": [{"decision": a.decision, "reasons": json.loads(a.reasons),
                         "confidence": a.confidence, "model": a.model, "created_at": a.created_at}
                        for a in trail],
    }


@router.post("/{job_id}/approve")
def approve(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Post to SAP. On failure (after retries) stays in the queue; 502 with the reason."""
    job = _get_pending(db, job_id)
    result, ok = post_job(db, job)
    db.commit()
    if not ok:
        raise HTTPException(502, result.reasons[0])
    return {"id": job.id, "review_status": job.review_status, "sap_doc_number": job.sap_doc_number}


@router.post("/{job_id}/correct")
def correct(job_id: str, body: CorrectBody, db: Session = Depends(get_db)) -> dict:
    """Edit fields, re-run the deterministic validators, keep original vs corrected values."""
    job = _get_pending(db, job_id)
    original = json.loads(job.fields_json)
    merged = {**effective_fields(job).model_dump(), **body.fields}
    try:
        fields = InvoiceFields.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json())) from exc

    new = fields.model_dump()
    changes = [{"field": k, "original": original.get(k), "corrected": v}
               for k, v in new.items() if v != original.get(k)]
    job.corrected_fields_json = fields.model_dump_json()
    job.corrections_json = json.dumps(changes, default=str)

    job.dup_hash = duplicate_hash(fields.vendor_gstin, fields.vendor_name,
                                  fields.invoice_number, fields.grand_total)
    ctx = ToolContext(fields, db, job.id)
    results = {tool_key(n, a): run_tool(n, a, ctx) for n, a in REQUIRED_CALLS}
    job.checks_json = json.dumps({"results": results, "backfilled": [], "error": None,
                                  "source": "reviewer_correction"}, default=str)
    routed = route(fields, results)
    _audit(db, job, "corrected",
           [f"{c['field']}: {c['original']!r} -> {c['corrected']!r}" for c in changes]
           + [f"Re-validation: {routed.decision}"] + routed.reasons)
    db.commit()
    return {"id": job.id, "corrections": changes, "validation": {
        "decision": routed.decision, "reasons": routed.reasons, "checks": results}}


@router.post("/{job_id}/reject")
def reject(job_id: str, body: RejectBody | None = None, db: Session = Depends(get_db)) -> dict:
    """Reject the invoice; nothing is posted."""
    job = _get_pending(db, job_id)
    job.decision = Decision.REJECT
    job.review_status = ReviewStatus.REJECTED
    _audit(db, job, Decision.REJECT, ["Rejected by reviewer" + (
        f": {body.reason}" if body and body.reason else "")])
    db.commit()
    return {"id": job.id, "review_status": job.review_status}
