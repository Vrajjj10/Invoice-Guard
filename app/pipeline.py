"""Background job processing. Later phases add LLM extraction, validation and routing."""

import logging
from pathlib import Path

from app.db.models import Job, JobStatus
from app.db.session import SessionLocal
from app.extraction.text import extract_text

log = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.PROCESSING
        db.commit()

        try:
            result = extract_text(Path(job.stored_path).read_bytes())
            job.extracted_text = result.text
            job.extraction_method = result.method
            job.ocr_confidence = result.ocr_confidence
            job.page_count = result.page_count
            job.extraction_ms = result.elapsed_ms
            job.status = JobStatus.DONE
        except Exception as exc:  # record failure on the job instead of losing it
            log.exception("Job %s failed", job_id)
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"[:500]
        db.commit()
    finally:
        db.close()
