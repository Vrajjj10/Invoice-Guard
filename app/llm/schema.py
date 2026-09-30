"""Pydantic schema for the single extraction call (also used as Gemini's response schema)."""

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    hsn: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class InvoiceFields(BaseModel):
    is_invoice: bool = Field(description="False if the document is not an invoice")
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    buyer_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = Field(default=None, description="YYYY-MM-DD")
    currency: str | None = None
    line_items: list[LineItem] = []
    subtotal: float | None = None
    cgst: float | None = None
    sgst: float | None = None
    igst: float | None = None
    grand_total: float | None = None
    confidence: float = Field(ge=0, le=1, description="Overall extraction confidence 0-1")
    reason: str = Field(default="", description="One short sentence on doubts, if any")
