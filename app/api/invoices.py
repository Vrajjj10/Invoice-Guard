"""Upload + job status endpoints."""

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuditLog, Job, JobStatus
from app.db.session import get_db
from app.extraction.text import UnsupportedFileError, detect_file_type
from app.pipeline import process_job
from app.security import limit_uploads, require_api_key

router = APIRouter(tags=["invoices"])


class UploadResponse(BaseModel):
    job_id: str
    status: str
    cached: bool
    file_hash: str


class JobResponse(BaseModel):
    job_id: str
    filename: str
    status: str
    error: str | None
    extraction_method: str | None
    ocr_confidence: float | None
    page_count: int | None
    extraction_ms: int | None
    extracted_text: str | None
    fields: dict | None
    llm_confidence: float | None
    llm_model: str | None
    llm_escalated: bool | None
    llm_tokens: int | None
    llm_ms: int | None
    checks: dict | None
    agent_notes: str | None
    agent_iterations: int | None
    agent_tokens: int | None
    decision: str | None
    decision_reasons: list[str] | None


@router.post(
    "/invoices/upload",
    response_model=UploadResponse,
    status_code=202,
    dependencies=[Depends(require_api_key), Depends(limit_uploads)],
)
async def upload_invoice(
    file: UploadFile, background: BackgroundTasks, db: Session = Depends(get_db)
) -> UploadResponse:
    max_bytes = get_settings().max_upload_bytes
    data = await file.read(max_bytes + 1)  # never buffer more than the limit
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > max_bytes:
        raise HTTPException(413, f"File too large (max {max_bytes // (1024 * 1024)} MB)")
    try:
        file_type = detect_file_type(data)
    except UnsupportedFileError as exc:
        raise HTTPException(415, str(exc)) from exc

    file_hash = hashlib.sha256(data).hexdigest()

    # Cache: same bytes already processed (or in progress) -> reuse that job
    existing = db.scalars(
        select(Job)
        .where(Job.file_hash == file_hash, Job.status != JobStatus.FAILED)
        .order_by(Job.created_at.desc())
    ).first()
    if existing:
        return UploadResponse(
            job_id=existing.id, status=existing.status, cached=True, file_hash=file_hash
        )

    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored = upload_dir / f"{file_hash}.{file_type}"
    stored.write_bytes(data)

    job = Job(
        file_hash=file_hash,
        filename=file.filename or "upload",
        file_type=file_type,
        stored_path=str(stored),
    )
    db.add(job)
    db.commit()

    background.add_task(process_job, job.id)
    return UploadResponse(job_id=job.id, status=job.status, cached=False, file_hash=file_hash)


@router.get(
    "/jobs/{job_id}", response_model=JobResponse, dependencies=[Depends(require_api_key)]
)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return JobResponse(
        job_id=job.id,
        filename=job.filename,
        status=job.status,
        error=job.error,
        extraction_method=job.extraction_method,
        ocr_confidence=job.ocr_confidence,
        page_count=job.page_count,
        extraction_ms=job.extraction_ms,
        extracted_text=job.extracted_text,
        fields=json.loads(job.fields_json) if job.fields_json else None,
        llm_confidence=job.llm_confidence,
        llm_model=job.llm_model,
        llm_escalated=job.llm_escalated,
        llm_tokens=job.llm_tokens,
        llm_ms=job.llm_ms,
        checks=json.loads(job.checks_json) if job.checks_json else None,
        agent_notes=job.agent_notes,
        agent_iterations=job.agent_iterations,
        agent_tokens=job.agent_tokens,
        decision=job.decision,
        decision_reasons=_latest_reasons(db, job.id),
    )


def _latest_reasons(db: Session, job_id: str) -> list[str] | None:
    row = db.scalars(
        select(AuditLog).where(AuditLog.job_id == job_id).order_by(AuditLog.id.desc())
    ).first()
    return json.loads(row.reasons) if row else None
