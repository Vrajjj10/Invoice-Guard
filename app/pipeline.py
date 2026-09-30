"""Background job processing: extraction -> LLM fields -> agent validation."""

import json
import logging
from pathlib import Path

from app.agent.loop import run_agent
from app.agent.tools import ToolContext
from app.db.models import Job, JobStatus
from app.db.session import SessionLocal
from app.extraction.text import extract_text
from app.llm.client import extract_invoice
from app.validators import duplicate_hash

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
            llm = extract_invoice(result.text, result.ocr_confidence)
            job.fields_json = llm.fields.model_dump_json()
            job.llm_confidence = llm.fields.confidence
            job.llm_model = llm.model
            job.llm_escalated = llm.escalated
            job.llm_tokens = llm.tokens
            job.llm_ms = llm.elapsed_ms
            f = llm.fields
            if f.is_invoice:
                job.dup_hash = duplicate_hash(
                    f.vendor_gstin, f.vendor_name, f.invoice_number, f.grand_total
                )
                agent = run_agent(ToolContext(f, db, job.id))
                job.checks_json = json.dumps(
                    {"results": agent.results, "backfilled": agent.backfilled,
                     "error": agent.error},
                    default=str,
                )
                job.agent_notes = agent.notes
                job.agent_iterations = agent.iterations
                job.agent_tokens = agent.tokens
            job.status = JobStatus.DONE
        except Exception as exc:  # record failure on the job instead of losing it
            log.exception("Job %s failed", job_id)
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"[:500]
        db.commit()
    finally:
        db.close()
