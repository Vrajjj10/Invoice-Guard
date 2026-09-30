"""SAP HTTP client; base URL from settings so a real endpoint can be swapped in."""

import logging
import time

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class SapError(Exception):
    """SAP call failed; `retryable` is False for 4xx (payload rejected)."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def post_supplier_invoice(payload: dict) -> str:
    """One POST attempt; returns the SAP document number."""
    s = get_settings()
    url = s.sap_base_url.rstrip("/") + "/supplier-invoices"
    try:
        r = httpx.post(url, json=payload, timeout=s.sap_timeout)
    except httpx.HTTPError as exc:
        raise SapError(f"{type(exc).__name__}: {exc}") from exc
    if r.status_code >= 500:
        raise SapError(f"SAP returned {r.status_code}")
    if r.status_code >= 400:
        raise SapError(f"SAP rejected payload ({r.status_code}): {r.text[:200]}", retryable=False)
    try:
        return r.json()["document_number"]
    except (ValueError, KeyError) as exc:
        raise SapError("SAP response missing document_number") from exc


def post_with_retry(payload: dict) -> str:
    """Retry transient failures with exponential backoff; raises the last SapError."""
    s = get_settings()
    attempts = max(1, s.sap_max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return post_supplier_invoice(payload)
        except SapError as exc:
            log.warning("SAP post attempt %d/%d failed: %s", attempt, attempts, exc)
            if not exc.retryable or attempt == attempts:
                raise
            time.sleep(s.sap_retry_delay * 2 ** (attempt - 1))
    raise SapError("unreachable")
