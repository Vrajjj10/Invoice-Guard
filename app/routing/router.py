"""Decision router: approve / manual_review / reject from agent checks + confidence."""

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuditLog, Decision, Job
from app.llm.schema import InvoiceFields


@dataclass
class RouteResult:
    decision: str
    reasons: list[str] = field(default_factory=list)
    confidence: float | None = None


def route(fields: InvoiceFields, results: dict[str, dict]) -> RouteResult:
    """Any reject reason -> reject; else any review reason -> manual_review; else approve."""
    s = get_settings()
    conf = fields.confidence
    if not fields.is_invoice:
        why = fields.reason or "document is not an invoice"
        return RouteResult(Decision.REJECT, [f"Not an invoice: {why}"], conf)

    reject: list[str] = []
    review: list[str] = []
    if conf < s.reject_below_confidence:
        reject.append(f"Extraction confidence {conf:.2f} below {s.reject_below_confidence:.2f}")
    elif conf < s.approve_min_confidence:
        review.append(f"Extraction confidence {conf:.2f} below {s.approve_min_confidence:.2f}")

    dup = results.get("check_duplicate", {})
    if dup.get("duplicate"):
        reject.append(f"Duplicate of job(s) {', '.join(dup.get('matching_jobs', []))}")
    elif dup.get("ok") is None:
        review.append("Duplicate check not possible: " + dup.get("detail", "missing data"))

    vendor = results.get("lookup_vendor", {})
    if not vendor.get("found"):
        review.append("Vendor not in vendor master")
    else:
        v = vendor["vendor"]
        if v.get("status") != "active":
            reject.append(f"Vendor {v['name']} is {v.get('status')}")
        if vendor.get("name_matches") is False or vendor.get("gstin_matches") is False:
            review.append("Vendor name and GSTIN do not match the master record")

    for key, label, required in (("validate_totals", "Totals/tax", True),
                                 ("validate_gstin:vendor", "Vendor GSTIN", True),
                                 ("validate_gstin:buyer", "Buyer GSTIN", False)):
        r = results.get(key, {})
        if "error" in r:
            review.append(f"{label} check errored: {r['error']}")
        elif r.get("ok") is False:
            review.append(f"{label} failed: {r.get('detail', 'see checks')}")
        elif r.get("ok") is None and required:
            review.append(f"{label} not checkable: {r.get('detail', 'missing data')}")

    an = results.get("flag_anomaly", {})
    if "error" in an:
        review.append(f"Anomaly check errored: {an['error']}")
    elif an.get("anomaly"):
        review.append(f"Anomalous amount (score {an.get('score')}): {an.get('reason')}")

    if reject:
        return RouteResult(Decision.REJECT, reject + review, conf)
    if review:
        return RouteResult(Decision.MANUAL_REVIEW, review, conf)
    return RouteResult(Decision.APPROVE, ["All checks passed"], conf)


def record_decision(db: Session, job: Job, result: RouteResult) -> AuditLog:
    """Set the decision on the job and add an audit row (commit left to the caller)."""
    job.decision = result.decision
    row = AuditLog(job_id=job.id, decision=result.decision, reasons=json.dumps(result.reasons),
                   confidence=result.confidence, model=job.llm_model)
    db.add(row)
    return row
