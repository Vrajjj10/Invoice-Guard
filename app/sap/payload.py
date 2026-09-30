"""Builds the SAP supplier-invoice payload from extracted invoice fields."""

from datetime import date

from app.config import get_settings
from app.llm.schema import InvoiceFields


class PayloadError(ValueError):
    """Invoice lacks data SAP requires."""


def build_payload(fields: InvoiceFields, posting_date: date | None = None) -> dict:
    """Map InvoiceFields to the SAP payload; raises PayloadError if required data is missing."""
    s = get_settings()
    vendor = fields.vendor_gstin or fields.vendor_name
    required = (("vendor", vendor), ("invoice_number", fields.invoice_number),
                ("invoice_date", fields.invoice_date), ("grand_total", fields.grand_total))
    missing = [name for name, value in required if not value]
    if missing:
        raise PayloadError("missing " + ", ".join(missing))
    items = [
        {"item": i, "description": li.description, "amount": li.amount or 0.0,
         "gl_account": s.sap_gl_account, "tax_code": s.sap_tax_code}
        for i, li in enumerate(fields.line_items, 1)
    ] or [{"item": 1, "description": "Invoice total", "amount": fields.subtotal or 0.0,
           "gl_account": s.sap_gl_account, "tax_code": s.sap_tax_code}]
    return {
        "vendor": vendor,
        "company_code": s.sap_company_code,
        "posting_date": (posting_date or date.today()).isoformat(),
        "document_date": fields.invoice_date,
        "reference": fields.invoice_number,
        "currency": fields.currency or "INR",
        "gl_account": s.sap_gl_account,
        "tax_code": s.sap_tax_code,
        "total": fields.grand_total,
        "items": items,
    }
