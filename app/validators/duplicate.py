"""Duplicate-invoice fingerprint: vendor + invoice number + amount."""

import hashlib
import re

from app.validators.gstin import normalize_gstin


def _norm_text(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def duplicate_hash(
    vendor_gstin: str | None, vendor_name: str | None, invoice_number: str | None,
    amount: float | None,
) -> str | None:
    """SHA-256 of normalized vendor (GSTIN, else name), invoice no, amount; None if incomplete."""
    vendor = normalize_gstin(vendor_gstin) or _norm_text(vendor_name)
    inv = _norm_text(invoice_number)
    if not vendor or not inv or amount is None:
        return None
    key = f"{vendor}|{inv}|{amount:.2f}"
    return hashlib.sha256(key.encode()).hexdigest()
