"""Arithmetic checks: line items, subtotal, GST split, grand total."""

from app.llm.schema import InvoiceFields
from app.validators.gstin import normalize_gstin

TOLERANCE = 1.0  # INR; absorbs per-line rounding
GST_RATES = {0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0, 40.0}  # percent
RATE_TOLERANCE = 0.1  # percentage points


def _close(a: float, b: float, tol: float = TOLERANCE) -> bool:
    return abs(a - b) <= tol


def _result(name: str, ok: bool | None, detail: str) -> dict:
    return {"check": name, "ok": ok, "detail": detail}


def check_line_items(fields: InvoiceFields) -> list[dict]:
    """Each qty*rate == amount; sum(amounts) == subtotal."""
    out = []
    for i, it in enumerate(fields.line_items):
        if None in (it.quantity, it.unit_price, it.amount):
            continue
        exp = it.quantity * it.unit_price
        if not _close(exp, it.amount):
            out.append(_result(f"line_{i + 1}", False, f"qty*rate={exp:.2f} != {it.amount:.2f}"))
    amounts = [it.amount for it in fields.line_items if it.amount is not None]
    if not amounts or fields.subtotal is None:
        out.append(_result("line_sum", None, "no line amounts or subtotal"))
    elif len(amounts) < len(fields.line_items):
        out.append(_result("line_sum", None, "some line amounts missing"))
    else:
        s = sum(amounts)
        ok = _close(s, fields.subtotal)
        out.append(_result("line_sum", ok, f"sum={s:.2f} subtotal={fields.subtotal:.2f}"))
    return out


def is_intra_state(vendor_gstin: str | None, buyer_gstin: str | None) -> bool | None:
    """Same GSTIN state code -> intra-state; None if unknown."""
    v, b = normalize_gstin(vendor_gstin)[:2], normalize_gstin(buyer_gstin)[:2]
    if len(v) < 2 or len(b) < 2:
        return None
    return v == b


def check_tax_split(fields: InvoiceFields) -> dict:
    """Intra-state: CGST == SGST, no IGST. Inter-state: IGST only."""
    cgst, sgst, igst = fields.cgst or 0.0, fields.sgst or 0.0, fields.igst or 0.0
    intra = is_intra_state(fields.vendor_gstin, fields.buyer_gstin)
    if intra is None:
        if (cgst or sgst) and igst:
            return _result("tax_split", False, "both CGST/SGST and IGST charged")
        return _result("tax_split", None, "state unknown (GSTIN missing)")
    if intra:
        if igst:
            return _result("tax_split", False, "intra-state invoice charges IGST")
        if not _close(cgst, sgst, 0.01):
            return _result("tax_split", False, f"CGST {cgst:.2f} != SGST {sgst:.2f}")
        return _result("tax_split", True, "intra-state CGST+SGST")
    if cgst or sgst:
        return _result("tax_split", False, "inter-state invoice charges CGST/SGST")
    return _result("tax_split", True, "inter-state IGST")


def check_tax_rate(fields: InvoiceFields) -> dict:
    """Effective GST rate should be a standard slab (mixed-rate invoices may fail)."""
    if not fields.subtotal:
        return _result("tax_rate", None, "no subtotal")
    tax = (fields.cgst or 0) + (fields.sgst or 0) + (fields.igst or 0)
    rate = tax / fields.subtotal * 100
    ok = any(abs(rate - r) <= RATE_TOLERANCE for r in GST_RATES)
    return _result("tax_rate", ok, f"effective rate {rate:.2f}%")


def check_grand_total(fields: InvoiceFields) -> dict:
    """subtotal + taxes == grand total."""
    if fields.subtotal is None or fields.grand_total is None:
        return _result("grand_total", None, "subtotal or grand total missing")
    exp = fields.subtotal + (fields.cgst or 0) + (fields.sgst or 0) + (fields.igst or 0)
    ok = _close(exp, fields.grand_total)
    return _result("grand_total", ok, f"expected {exp:.2f} got {fields.grand_total:.2f}")


def validate_totals(fields: InvoiceFields) -> dict:
    """All arithmetic checks; ok is False if any fail, None if none could run."""
    checks = [
        *check_line_items(fields),
        check_tax_split(fields),
        check_tax_rate(fields),
        check_grand_total(fields),
    ]
    states = [c["ok"] for c in checks]
    ok = False if False in states else (True if True in states else None)
    return {"ok": ok, "checks": checks}
