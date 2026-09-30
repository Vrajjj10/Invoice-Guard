from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from app import pipeline
from app.config import get_settings
from app.db.models import AuditLog, Decision, Job, JobStatus, ReviewStatus, SapDocument
from app.db.session import SessionLocal
from app.llm.client import LLMResult
from app.sap import client as sap_client
from app.sap.payload import PayloadError, build_payload
from tests.test_routing import GOOD
from tests.test_validators import _inv


@pytest.fixture(autouse=True)
def _sap_via_testclient(client, monkeypatch):
    """Route the SAP client's HTTP calls into the in-process app; no sleeping on retries."""
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return client.post(urlparse(url).path, json=json)

    monkeypatch.setattr(sap_client.httpx, "post", fake_post)
    monkeypatch.setattr(get_settings(), "sap_retry_delay", 0)
    return calls


def _payload(**over):
    return {**build_payload(_inv(invoice_date="2026-03-01")), **over}


def _job(db=None, review=ReviewStatus.PENDING, **fields) -> str:
    db = SessionLocal()
    f = _inv(invoice_date="2026-03-01", **fields)
    job = Job(file_hash="h", filename="f.pdf", file_type="pdf", stored_path="x",
              status=JobStatus.DONE, fields_json=f.model_dump_json(), llm_confidence=0.6,
              decision=Decision.MANUAL_REVIEW, review_status=review)
    db.add(job)
    db.commit()
    jid = job.id
    db.close()
    return jid


def _load(jid) -> Job:
    db = SessionLocal()
    job = db.get(Job, jid)
    db.close()
    return job


# --- payload + mock SAP ---

def test_payload_shape():
    p = build_payload(_inv(invoice_date="2026-03-01"))
    assert p["vendor"] == "27AAPFU0939F1ZV" and p["reference"] == "SGT/2026/0457"
    assert p["document_date"] == "2026-03-01" and p["total"] == 15399
    assert p["currency"] == "INR" and len(p["items"]) == 3
    assert p["company_code"] == get_settings().sap_company_code


def test_payload_missing_data():
    with pytest.raises(PayloadError, match="invoice_date"):
        build_payload(_inv())  # no invoice_date


def test_mock_sap_post_and_fetch(client):
    body = _payload(reference="M-1")
    r = client.post("/mock-sap/supplier-invoices", json=body)
    assert r.status_code == 201
    doc = r.json()["document_number"]
    assert len(doc) == 10 and doc.startswith("51")
    assert client.get(f"/mock-sap/supplier-invoices/{doc}").json()["payload"]["reference"] == "M-1"
    # idempotent on vendor + reference
    assert client.post("/mock-sap/supplier-invoices", json=body).json()["document_number"] == doc
    db = SessionLocal()
    assert db.scalars(select(SapDocument).where(SapDocument.reference == "M-1")).one()
    db.close()


@pytest.mark.parametrize("key", ["vendor", "company_code", "posting_date", "document_date",
                                 "reference", "currency", "gl_account", "tax_code", "total",
                                 "items"])
def test_mock_sap_rejects_missing_field(client, key):
    body = _payload()
    del body[key]
    assert client.post("/mock-sap/supplier-invoices", json=body).status_code == 422


def test_mock_sap_rejects_empty_and_bad_values(client):
    assert client.post("/mock-sap/supplier-invoices", json=_payload(vendor="")).status_code == 422
    assert client.post("/mock-sap/supplier-invoices", json=_payload(items=[])).status_code == 422
    assert client.post("/mock-sap/supplier-invoices", json=_payload(total=0)).status_code == 422
    assert client.get("/mock-sap/supplier-invoices/nope").status_code == 404


# --- retry behaviour ---

def test_retry_then_success(monkeypatch):
    n = []

    def flaky(payload):
        n.append(1)
        if len(n) < 3:
            raise sap_client.SapError("503")
        return "5100000042"

    monkeypatch.setattr(sap_client, "post_supplier_invoice", flaky)
    assert sap_client.post_with_retry({}) == "5100000042" and len(n) == 3


def test_no_retry_on_rejected_payload(monkeypatch):
    n = []

    def rejected(payload):
        n.append(1)
        raise sap_client.SapError("422", retryable=False)

    monkeypatch.setattr(sap_client, "post_supplier_invoice", rejected)
    with pytest.raises(sap_client.SapError):
        sap_client.post_with_retry({})
    assert len(n) == 1


# --- pipeline wiring ---

def _run_pipeline(monkeypatch, fields, tmp_path: Path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "extract_text", lambda b: type("R", (), {
        "text": "t", "method": "text", "ocr_confidence": 1.0, "page_count": 1,
        "elapsed_ms": 1})())
    monkeypatch.setattr(pipeline, "extract_invoice",
                        lambda t, c: LLMResult(fields, "m", False, 1, 1))
    monkeypatch.setattr(pipeline, "run_agent", lambda ctx: type("A", (), {
        "results": GOOD, "backfilled": [], "error": None, "notes": "", "iterations": 1,
        "tokens": 1})())
    db = SessionLocal()
    job = Job(file_hash="p", filename="p.pdf", file_type="pdf", stored_path=str(f))
    db.add(job)
    db.commit()
    jid = job.id
    db.close()
    pipeline.process_job(jid)
    return jid


def test_approved_invoice_posted_and_audited(monkeypatch, tmp_path):
    jid = _run_pipeline(monkeypatch, _inv(invoice_number="P-OK", invoice_date="2026-03-01",
                                          confidence=0.95), tmp_path)
    job = _load(jid)
    assert job.decision == Decision.APPROVE and job.sap_doc_number.startswith("51")
    db = SessionLocal()
    rows = db.scalars(select(AuditLog).where(AuditLog.job_id == jid).order_by(AuditLog.id)).all()
    db.close()
    assert job.sap_doc_number in rows[-1].reasons


def test_sap_failure_moves_to_manual_review(monkeypatch, tmp_path, _sap_via_testclient):
    def down(payload):
        _sap_via_testclient.append(payload)
        raise sap_client.SapError("connection refused")

    monkeypatch.setattr(sap_client, "post_supplier_invoice", down)
    jid = _run_pipeline(monkeypatch, _inv(invoice_number="P-FAIL", invoice_date="2026-03-01",
                                          confidence=0.95), tmp_path)
    job = _load(jid)
    assert len(_sap_via_testclient) == get_settings().sap_max_attempts
    assert job.decision == Decision.MANUAL_REVIEW and job.review_status == ReviewStatus.PENDING
    assert job.sap_doc_number is None and "connection refused" in job.sap_error


# --- review endpoints ---

def test_list_and_filter(client):
    a = _job(invoice_number="L-1")
    b = _job(invoice_number="L-2", review=ReviewStatus.REJECTED)
    ids = {r["id"] for r in client.get("/reviews").json()}
    assert {a, b} <= ids
    pending = {r["id"] for r in client.get("/reviews?status=pending").json()}
    assert a in pending and b not in pending
    assert client.get("/reviews?status=bogus").status_code == 422


def test_detail_has_fields_reasons_and_audit(client):
    jid = _job(invoice_number="D-1")
    db = SessionLocal()
    db.add(AuditLog(job_id=jid, decision=Decision.MANUAL_REVIEW, reasons='["low confidence"]',
                    confidence=0.6))
    db.commit()
    db.close()
    d = client.get(f"/reviews/{jid}").json()
    assert d["fields"]["invoice_number"] == "D-1" and d["reasons"] == ["low confidence"]
    assert d["audit_trail"][0]["decision"] == Decision.MANUAL_REVIEW
    assert client.get("/reviews/nope").status_code == 404


def test_approve_posts_to_sap(client):
    jid = _job(invoice_number="A-1")
    r = client.post(f"/reviews/{jid}/approve")
    assert r.status_code == 200 and r.json()["review_status"] == ReviewStatus.APPROVED
    job = _load(jid)
    assert job.sap_doc_number == r.json()["sap_doc_number"] and job.decision == Decision.APPROVE
    assert client.get(f"/reviews/{jid}").json()["audit_trail"][-1]["decision"] == Decision.APPROVE
    assert client.post(f"/reviews/{jid}/approve").status_code == 409  # no double post


def test_approve_sap_failure_stays_pending(client, monkeypatch):
    jid = _job(invoice_number="A-2")

    def down(payload):
        raise sap_client.SapError("timeout")

    monkeypatch.setattr(sap_client, "post_supplier_invoice", down)
    r = client.post(f"/reviews/{jid}/approve")
    assert r.status_code == 502 and "timeout" in r.json()["detail"]
    job = _load(jid)
    assert job.review_status == ReviewStatus.PENDING and job.sap_doc_number is None


def test_approve_missing_data_fails_cleanly(client):
    jid = _job(invoice_number=None)
    assert client.post(f"/reviews/{jid}/approve").status_code == 502


def test_correct_stores_original_vs_corrected_and_revalidates(client):
    jid = _job(invoice_number="C-1", grand_total=99999)  # wrong total
    r = client.post(f"/reviews/{jid}/correct", json={"fields": {"grand_total": 15399}})
    assert r.status_code == 200
    body = r.json()
    assert body["corrections"] == [{"field": "grand_total", "original": 99999.0,
                                    "corrected": 15399.0}]
    assert body["validation"]["checks"]["validate_totals"]["ok"] is True
    d = client.get(f"/reviews/{jid}").json()
    assert d["fields"]["grand_total"] == 99999 and d["corrected_fields"]["grand_total"] == 15399
    assert d["audit_trail"][-1]["decision"] == "corrected"
    # approve posts the corrected values
    doc = client.post(f"/reviews/{jid}/approve").json()["sap_doc_number"]
    assert client.get(f"/mock-sap/supplier-invoices/{doc}").json()["payload"]["total"] == 15399


def test_correct_bad_value_422_and_no_change(client):
    jid = _job(invoice_number="C-2")
    assert client.post(f"/reviews/{jid}/correct",
                       json={"fields": {"grand_total": "abc"}}).status_code == 422
    assert _load(jid).corrected_fields_json is None


def test_reject(client):
    jid = _job(invoice_number="R-1")
    r = client.post(f"/reviews/{jid}/reject", json={"reason": "not ours"})
    assert r.json()["review_status"] == ReviewStatus.REJECTED
    trail = client.get(f"/reviews/{jid}").json()["audit_trail"]
    assert trail[-1]["decision"] == Decision.REJECT and "not ours" in trail[-1]["reasons"][0]
    assert client.post(f"/reviews/{jid}/reject").status_code == 409
    assert client.post(f"/reviews/{jid}/approve").status_code == 409
    assert _load(jid).decision == Decision.REJECT
