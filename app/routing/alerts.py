"""Alert stub: console log, plus Slack webhook when SLACK_WEBHOOK_URL is set."""

import logging

import httpx

from app.config import get_settings
from app.db.models import Decision

log = logging.getLogger(__name__)
ALERT_DECISIONS = {Decision.MANUAL_REVIEW, Decision.REJECT}


def send_alert(job_id: str, decision: str, reasons: list[str]) -> None:
    """Notify for manual_review/reject only. Never raises."""
    if decision not in ALERT_DECISIONS:
        return
    text = f"[InvoiceGuard] job {job_id}: {decision.upper()} - " + "; ".join(reasons)
    log.warning(text)
    url = get_settings().slack_webhook_url
    if not url:
        return
    try:
        httpx.post(url, json={"text": text}, timeout=5).raise_for_status()
    except Exception as exc:
        log.error("Slack alert failed: %s", exc)
