"""Generate one sample GST invoice (digital PDF + scanned-style PDF + PNG) for OCR checks.

Usage: python scripts/make_sample.py
"""

import io
import random
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path("data/samples")


def make_invoice_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, h = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, h - 60, "TAX INVOICE")
    c.setFont("Helvetica", 10)
    lines = [
        "Shree Ganesh Traders Pvt Ltd",
        "12 MG Road, Pune, Maharashtra 411001",
        "GSTIN: 27AAPFU0939F1ZV",
        "",
        "Invoice No: SGT/2026/0457        Invoice Date: 14-09-2026",
        "Bill To: Dimexon Components LLP, Mumbai, Maharashtra",
        "Buyer GSTIN: 27AAACD1234E1Z5",
    ]
    y = h - 90
    for line in lines:
        c.drawString(40, y, line)
        y -= 15

    y -= 10
    c.setFont("Helvetica-Bold", 10)
    for x, t in [(40, "Description"), (260, "HSN"), (320, "Qty"), (370, "Rate"), (460, "Amount")]:
        c.drawString(x, y, t)
    c.setFont("Helvetica", 10)
    items = [
        ("Steel Bolts M8", "7318", 500, 4.50),
        ("Hydraulic Hose 2m", "4009", 20, 350.00),
        ("Bearing 6204ZZ", "8482", 40, 95.00),
    ]
    subtotal = 0.0
    for desc, hsn, qty, rate in items:
        y -= 16
        amt = qty * rate
        subtotal += amt
        for x, t in [(40, desc), (260, hsn), (320, str(qty)), (370, f"{rate:.2f}"),
                     (460, f"{amt:,.2f}")]:
            c.drawString(x, y, t)

    cgst = sgst = round(subtotal * 0.09, 2)
    total = subtotal + cgst + sgst
    y -= 30
    for label, val in [("Subtotal", subtotal), ("CGST @ 9%", cgst), ("SGST @ 9%", sgst),
                       ("Grand Total (INR)", total)]:
        c.drawString(340, y, label)
        c.drawRightString(530, y, f"{val:,.2f}")
        y -= 16
    c.drawString(40, y - 20, "Payment terms: Net 30. Bank: HDFC Bank, A/c 50200012345678")
    c.save()


def scanned_image(pdf_path: Path, dpi: int = 150) -> Image.Image:
    """Rasterize page 1 and degrade it: grayscale, slight rotation, blur, noise."""
    with fitz.open(pdf_path) as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    img = img.rotate(random.uniform(-1.5, 1.5), expand=True, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    arr = np.array(img, dtype=np.int16)
    arr += np.random.normal(0, 12, arr.shape).astype(np.int16)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def main() -> None:
    random.seed(7)
    np.random.seed(7)
    OUT.mkdir(parents=True, exist_ok=True)
    digital = OUT / "sample_digital.pdf"
    make_invoice_pdf(digital)

    img = scanned_image(digital)
    img.save(OUT / "sample_scanned.png")
    img.convert("RGB").save(OUT / "sample_scanned.pdf")  # image-only PDF, no text layer
    print("Wrote:", digital, OUT / "sample_scanned.png", OUT / "sample_scanned.pdf")


if __name__ == "__main__":
    main()
