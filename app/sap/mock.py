"""Mock SAP: supplier-invoice create endpoint (stands in for a real S/4HANA API)."""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SapDocument
from app.db.session import get_db

router = APIRouter(prefix="/mock-sap", tags=["mock-sap"])


class SapItem(BaseModel):
    item: int
    description: str = Field(min_length=1)
    amount: float
    gl_account: str = Field(min_length=1)
    tax_code: str = Field(min_length=1)


class SupplierInvoice(BaseModel):
    vendor: str = Field(min_length=1)
    company_code: str = Field(min_length=1, max_length=4)
    posting_date: date
    document_date: date
    reference: str = Field(min_length=1, max_length=40)
    currency: str = Field(min_length=3, max_length=3)
    gl_account: str = Field(min_length=1)
    tax_code: str = Field(min_length=1)
    total: float = Field(gt=0)
    items: list[SapItem] = Field(min_length=1)


def _doc_response(doc: SapDocument) -> dict:
    return {"document_number": doc.document_number, "fiscal_year": doc.fiscal_year,
            "company_code": doc.company_code, "status": "posted"}


@router.post("/supplier-invoices", status_code=201)
def create_supplier_invoice(inv: SupplierInvoice, db: Session = Depends(get_db)) -> dict:
    """Validate, store, return a fake document number. Idempotent per vendor + reference."""
    dup = db.scalars(select(SapDocument).where(
        SapDocument.company_code == inv.company_code, SapDocument.vendor == inv.vendor,
        SapDocument.reference == inv.reference)).first()
    if dup:
        return _doc_response(dup)
    doc = SapDocument(document_number="pending", fiscal_year=inv.posting_date.year,
                      company_code=inv.company_code, vendor=inv.vendor, reference=inv.reference,
                      payload_json=inv.model_dump_json())
    db.add(doc)
    db.flush()
    doc.document_number = f"51{doc.id:08d}"
    db.commit()
    return _doc_response(doc)


@router.get("/supplier-invoices/{document_number}")
def get_supplier_invoice(document_number: str, db: Session = Depends(get_db)) -> dict:
    doc = db.scalars(select(SapDocument).where(
        SapDocument.document_number == document_number)).first()
    if doc is None:
        raise HTTPException(404, "Document not found")
    return {**_doc_response(doc), "payload": json.loads(doc.payload_json)}
