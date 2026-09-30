import json

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Decision, Job
from app.db.session import SessionLocal, init_db
from app.routing import alerts, record_decision, route, send_alert
from tests.test_validators import _inv

GOOD = {
    "check_duplicate": {"ok": True, "duplicate": False},
    "validate_totals": {"ok": True},
    "validate_gstin:vendor": {"ok": True},
    "validate_gstin:buyer": {"ok": True},
    "lookup_vendor": {"found": True, "name_matches": True,
                      "vendor": {"name": "Shree Ganesh", "status": "active"}},
    "flag_anomaly": {"ok": True, "anomaly": False, "score": 0.4},
}


def _with(**over):
    return {**GOOD, **over}


def test_approve():
    r = route(_inv(confidence=0.95), GOOD)
    assert r.decision == Decision.APPROVE


def test_non_invoice_rejected_with_reason():
    r = route(_inv(is_invoice=False, reason="looks like a resume"), {})
    assert r.decision == Decision.REJECT
    assert "resume" in r.reasons[0]


def test_duplicate_rejected():
    r = route(_inv(), _with(check_duplicate={"ok": False, "duplicate": True,
                                             "matching_jobs": ["j1"]}))
    assert r.decision == Decision.REJECT and "j1" in r.reasons[0]


def test_blocked_vendor_rejected():
    v = {"found": True, "vendor": {"name": "Gujarat Polymers", "status": "blocked"}}
    assert route(_inv(), _with(lookup_vendor=v)).decision == Decision.REJECT


@pytest.mark.parametrize("conf,expected", [
    (0.95, Decision.APPROVE), (0.85, Decision.APPROVE), (0.6, Decision.MANUAL_REVIEW),
    (0.4, Decision.MANUAL_REVIEW), (0.39, Decision.REJECT),
])
def test_confidence_thresholds(conf, expected):
    assert route(_inv(confidence=conf), GOOD).decision == expected


def test_confidence_thresholds_configurable(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "approve_min_confidence", 0.99)
    assert route(_inv(confidence=0.95), GOOD).decision == Decision.MANUAL_REVIEW


@pytest.mark.parametrize("key,val", [
    ("validate_totals", {"ok": False, "detail": "grand total off"}),
    ("validate_gstin:vendor", {"ok": False, "detail": "checksum"}),
    ("validate_gstin:buyer", {"ok": False, "detail": "bad format"}),
    ("flag_anomaly", {"ok": False, "anomaly": True, "score": 0.7, "reason": "high"}),
    ("lookup_vendor", {"found": False}),
    ("validate_totals", {"error": "boom"}),
    ("validate_totals", {"ok": None, "detail": "missing data"}),
])
def test_problems_go_to_manual_review(key, val):
    assert route(_inv(), _with(**{key: val})).decision == Decision.MANUAL_REVIEW


def test_unchecked_anomaly_and_buyer_do_not_block():
    res = _with(flag_anomaly={"ok": None, "anomaly": None},
                **{"validate_gstin:buyer": {"ok": None}})
    assert route(_inv(), res).decision == Decision.APPROVE


def test_reject_includes_review_reasons():
    r = route(_inv(), _with(check_duplicate={"duplicate": True, "matching_jobs": ["j1"]},
                            validate_totals={"ok": False, "detail": "x"}))
    assert r.decision == Decision.REJECT and len(r.reasons) == 2


def test_audit_row_written():
    init_db()
    db = SessionLocal()
    job = Job(file_hash="h", filename="f.pdf", file_type="pdf", stored_path="x", llm_model="m1")
    db.add(job)
    db.flush()
    record_decision(db, job, route(_inv(confidence=0.6), GOOD))
    db.commit()
    row = db.scalars(select(AuditLog).where(AuditLog.job_id == job.id)).one()
    assert (row.decision, row.model, row.confidence) == (Decision.MANUAL_REVIEW, "m1", 0.6)
    assert json.loads(row.reasons) and row.created_at is not None
    assert job.decision == Decision.MANUAL_REVIEW
    db.close()


def test_alert_console_only(monkeypatch, caplog):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "slack_webhook_url", "")
    called = []
    monkeypatch.setattr(alerts.httpx, "post", lambda *a, **k: called.append(1))
    send_alert("j1", Decision.REJECT, ["dup"])
    assert "REJECT" in caplog.text and not called


def test_alert_slack_and_skips_approve(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "slack_webhook_url", "http://hook")
    sent = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(alerts.httpx, "post", lambda url, **k: sent.append((url, k)) or Resp())
    send_alert("j1", Decision.APPROVE, ["ok"])
    assert not sent
    send_alert("j2", Decision.MANUAL_REVIEW, ["low conf"])
    assert sent[0][0] == "http://hook" and "j2" in sent[0][1]["json"]["text"]


def test_alert_slack_failure_swallowed(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "slack_webhook_url", "http://hook")

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(alerts.httpx, "post", boom)
    send_alert("j1", Decision.REJECT, ["x"])
