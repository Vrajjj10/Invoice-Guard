import pytest

from app.api.invoices import build_stages
from app.config import get_settings
from app.db.models import AuditLog, Decision, Job, JobStatus
from app.db.session import SessionLocal


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_api_key", "")
    monkeypatch.setattr(get_settings(), "environment", "dev")


def _make(**kw) -> Job:
    db = SessionLocal()
    job = Job(file_hash="h", filename="f.pdf", file_type="pdf", stored_path="x", **kw)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, db


def _status(stages):
    return {s["key"]: s["status"] for s in stages}


def test_root_serves_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "InvoiceGuard" in r.text and "Invoices in. Verified decisions out." in r.text
    assert "innerHTML" not in r.text  # server data must only go through textContent
    assert client.get("/docs").status_code == 200
    assert client.get("/health").status_code == 200


def test_samples_whitelist(client):
    names = client.get("/samples").json()
    assert "1_clean.pdf" in names
    assert client.get("/samples/1_clean.pdf").status_code == 200
    assert client.get("/samples/..%2FREADME.md").status_code == 404
    assert client.get("/samples/README.md").status_code == 404


def test_stages_in_job_response(client):
    job, db = _make(status=JobStatus.DONE, extraction_ms=120, llm_ms=900,
                    fields_json='{"is_invoice": true}', checks_json="{}",
                    decision=Decision.APPROVE, sap_doc_number="5100000001")
    db.add(AuditLog(job_id=job.id, decision="approve", reasons="[]"))
    db.commit()
    body = client.get(f"/jobs/{job.id}").json()
    keys = ["upload", "ocr", "ai", "validation", "decision", "sap"]
    assert [s["key"] for s in body["stages"]] == keys
    assert set(_status(body["stages"]).values()) == {"done"}
    assert body["stages"][1]["ms"] == 120 and body["stages"][2]["ms"] == 900
    assert body["sap_doc_number"] == "5100000001"


def test_stages_active_failed_and_skipped(client):
    job, db = _make(status=JobStatus.PROCESSING, extraction_ms=50)
    assert _status(build_stages(db, job)) == {
        "upload": "done", "ocr": "done", "ai": "active",
        "validation": "pending", "decision": "pending", "sap": "pending"}
    job.status = JobStatus.FAILED
    assert _status(build_stages(db, job))["ai"] == "failed"

    review, db = _make(status=JobStatus.DONE, extraction_ms=1, llm_ms=1,
                       fields_json='{"is_invoice": false}', decision=Decision.REJECT)
    db.add(AuditLog(job_id=review.id, decision="reject", reasons="[]"))
    db.commit()
    st = _status(build_stages(db, review))
    assert st["validation"] == "skipped" and st["sap"] == "skipped" and st["decision"] == "done"


def test_stats(client):
    j1, db = _make(status=JobStatus.DONE, decision=Decision.APPROVE)
    _make(status=JobStatus.DONE, decision=Decision.MANUAL_REVIEW, review_status="pending")
    body = client.get("/stats").json()
    assert body["processed"] >= 2 and body["flagged"] >= 1 and body["approved"] >= 1
    assert 0 < body["auto_approved_pct"] < 100
    assert body["avg_processing_s"] is not None


def test_stats_requires_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_api_key", "secret")
    assert client.get("/stats").status_code == 401
    assert client.get("/stats", headers={"X-API-Key": "secret"}).status_code == 200


def test_audit_log_lists_all_jobs(client):
    a, db = _make(status=JobStatus.DONE)
    b, _ = _make(status=JobStatus.DONE)
    db.add(AuditLog(job_id=a.id, decision="approve", reasons="[]"))
    db.add(AuditLog(job_id=b.id, decision="reject", reasons='["Duplicate"]'))
    db.commit()
    rows = client.get("/audit?limit=2").json()
    assert [r["decision"] for r in rows] == ["reject", "approve"]
    assert rows[0]["filename"] == "f.pdf" and rows[0]["reasons"] == ["Duplicate"]
