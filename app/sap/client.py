"""SAP HTTP client; base URL from settings so a real endpoint can be swapped in."""

import logging
import os
import time
from urllib.parse import urlparse

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class SapError(Exception):
    """SAP call failed; `retryable` is False for 4xx (payload rejected)."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _is_local(url: str) -> bool:
    return urlparse(url).hostname in {"localhost", "127.0.0.1", "::1"}


def sap_base_url() -> str:
    """Configured URL, else this app's own mock (uvicorn binds $PORT; 8000 locally)."""
    configured = get_settings().sap_base_url.strip()
    return configured or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/mock-sap"


def post_supplier_invoice(payload: dict) -> str:
    """One POST attempt; returns the SAP document number."""
    s = get_settings()
    url = sap_base_url().rstrip("/") + "/supplier-invoices"
    # The bundled mock lives in this app and is key-protected; never send our key elsewhere
    headers = {"X-API-Key": s.demo_api_key} if s.demo_api_key and _is_local(url) else {}
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=s.sap_timeout)
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
