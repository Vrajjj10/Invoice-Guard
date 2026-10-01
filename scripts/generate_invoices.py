"""Generate the synthetic test set: 25 PDFs, ~8 scanned-style images, ground_truth.json.

All vendors, GSTINs and amounts are fictional (GSTINs follow the format and carry a valid
mod-36 check character, except the deliberately broken ones).

Usage: python scripts/generate_invoices.py [--out data/invoices] [--seed 7]
"""

import argparse
import io
import json
import math
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.validators.gstin import gstin_check_char  # noqa: E402

W, H = A4
BUYER_NAME = "Dimexon Components LLP"
BUYER_ADDR = "Plot 14, MIDC Andheri East, Mumbai, Maharashtra 400093"


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    base = f"{state}{pan}{entity}Z"
    return base + gstin_check_char(base)


BUYER_GSTIN = make_gstin("27", "AABFD4821K")


@dataclass
class Vendor:
    key: str
    name: str
    addr: str
    gstin: str
    state: str
    template: str  # classic | band | boxed | ledger
    typical: float  # typical subtotal-ish amount (INR), matches train_anomaly
    sigma: float
    items: list[tuple[str, str]]  # (description, HSN)
    rate: float = 18.0


# First two are existing entries of data/vendors.json (their GSTINs have valid checksums).
VENDORS = [
    Vendor("sgt", "Shree Ganesh Traders Pvt Ltd", "12 MG Road, Pune, Maharashtra 411001",
           "27AAPFU0939F1ZV", "27", "classic", 15_000, 0.35,
           [("Steel Bolts M8", "7318"), ("Hydraulic Hose 2m", "4009"), ("Bearing 6204ZZ", "8482"),
            ("Hex Nut M10", "7318"), ("Washer Set", "7318")]),
    Vendor("bis", "Bharat Industrial Supplies", "48 Peenya Ind Est, Bengaluru, Karnataka 560058",
           "29AAGCB7383J1Z4", "29", "band", 60_000, 0.40,
           [("Industrial Gloves (pair)", "6116"), ("Safety Helmet", "6506"),
            ("Cutting Disc 4in", "6805"), ("Welding Rod 3.15mm", "8311")]),
    Vendor("kft", "Konkan Fasteners & Tools", "Gala 7, Vasai East, Palghar, Maharashtra 401208",
           make_gstin("27", "AAKFK5512M"), "27", "boxed", 8_000, 0.25,
           [("Allen Key Set", "8205"), ("Anchor Bolt 12mm", "7318"), ("Torque Wrench", "8204"),
            ("Drill Bit Set HSS", "8207")]),
    Vendor("spc", "Sahyadri Packaging Co", "Survey 31, Chakan MIDC, Pune, Maharashtra 410501",
           make_gstin("27", "AASPS7340Q"), "27", "ledger", 35_000, 0.30,
           [("Corrugated Box 5-ply", "4819"), ("BOPP Tape 48mm", "3919"),
            ("Bubble Wrap Roll", "3921"), ("Stretch Film 500mm", "3920")]),
    Vendor("dhp", "Deccan Hydraulics Pvt Ltd", "Plot 22, Jeedimetla, Hyderabad, Telangana 500055",
           make_gstin("36", "AADCD9921P"), "36", "band", 90_000, 0.35,
           [("Hydraulic Cylinder 63mm", "8412"), ("Gear Pump 20cc", "8413"),
            ("Pressure Valve", "8481"), ("Seal Kit", "4016")]),
    Vendor("rom", "Rajputana Office Mart", "Shop 5, MI Road, Jaipur, Rajasthan 302001",
           make_gstin("08", "AARPR3078N"), "08", "classic", 5_000, 0.30,
           [("A4 Paper Ream", "4802"), ("Ball Pen Box", "9608"), ("Stapler Heavy Duty", "8472"),
            ("File Folder", "4820")], rate=12.0),
    Vendor("kel", "Kaveri Electricals LLP", "17 Ambattur Estate, Chennai, Tamil Nadu 600058",
           make_gstin("33", "AAKFK6644L"), "33", "boxed", 22_000, 0.40,
           [("MCB 32A", "8536"), ("Copper Cable 2.5mm (m)", "8544"), ("Contactor 25A", "8536"),
            ("LED Panel 40W", "9405")], rate=18.0),
    Vendor("hlb", "Himalaya Lubricants Ltd", "SCO 12, Phase 7, Mohali, Punjab 160055",
           make_gstin("03", "AAHCH2257R"), "03", "ledger", 250_000, 0.45,
           [("Hydraulic Oil 68 (210L)", "2710"), ("Gear Oil 90 (20L)", "2710"),
            ("Industrial Grease 18kg", "2710"), ("Coolant Concentrate 20L", "3820")], rate=28.0),
]
V = {v.key: v for v in VENDORS}


@dataclass
class Line:
    desc: str
    hsn: str
    qty: int
    rate: float
    amount: float


@dataclass
class Invoice:
    vendor: Vendor
    number: str
    date: date
    lines: list[Line]
    subtotal: float
    cgst: float
    sgst: float
    igst: float
    total: float
    tax_label_rate: float  # rate printed next to tax labels
    vendor_gstin_shown: str = ""
    template: str = ""
    category: str = "clean"
    note: str = ""


def inr(x: float, indian: bool) -> str:
    s = f"{x:.2f}"
    if not indian:
        return f"{x:,.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{whole}.{frac}"


def build_invoice(rng: random.Random, v: Vendor, seq: int, *, target: float | None = None,
                  rate: float | None = None, intra: bool | None = None) -> Invoice:
    if target is None:
        target = math.exp(rng.gauss(math.log(v.typical), v.sigma * 0.5))
    rate = v.rate if rate is None else rate
    if intra is None:
        intra = v.state == BUYER_GSTIN[:2]
    n = rng.randint(2, min(5, len(v.items)))
    weights = [rng.uniform(0.5, 2.0) for _ in range(n)]
    lines = []
    for (desc, hsn), w in zip(rng.sample(v.items, n), weights, strict=True):
        share = target * w / sum(weights)
        qty = rng.choice([2, 5, 10, 12, 20, 25, 40, 50, 100, 200])
        unit = round(share / qty, 2)
        lines.append(Line(desc, hsn, qty, unit, round(qty * unit, 2)))
    subtotal = round(sum(ln.amount for ln in lines), 2)
    tax = round(subtotal * rate / 100, 2)
    half = round(subtotal * rate / 200, 2)
    inv_date = date(2026, 8, 3) + timedelta(days=rng.randint(0, 55))
    prefix = "".join(c for c in v.name if c.isupper())[:3]
    inv = Invoice(v, f"{prefix}/26-27/{rng.randint(100, 999) + seq}", inv_date, lines, subtotal,
                  half if intra else 0.0, half if intra else 0.0, 0.0 if intra else tax,
                  subtotal + (2 * half if intra else tax), rate)
    inv.total = round(subtotal + inv.cgst + inv.sgst + inv.igst, 2)
    inv.vendor_gstin_shown = v.gstin
    inv.template = v.template
    return inv


# ----------------------------------------------------------------- rendering helpers

def _fmt_date(d: date, style: str) -> str:
    return {"dmy-": d.strftime("%d-%m-%Y"), "dmy/": d.strftime("%d/%m/%Y"),
            "long": d.strftime("%d %b %Y")}[style]


def _tax_rows(inv: Invoice, labels: dict, indian: bool) -> list[tuple[str, str]]:
    r = inv.tax_label_rate
    rows = [(labels["sub"], inr(inv.subtotal, indian))]
    if inv.igst:
        rows.append((f"{labels['igst']} @ {r:g}%", inr(inv.igst, indian)))
    if inv.cgst or inv.sgst:
        rows.append((f"{labels['cgst']} @ {r / 2:g}%", inr(inv.cgst, indian)))
        rows.append((f"{labels['sgst']} @ {r / 2:g}%", inr(inv.sgst, indian)))
    rows.append((labels["total"], inr(inv.total, indian)))
    return rows


def render_classic(c: canvas.Canvas, inv: Invoice) -> None:
    v = inv.vendor
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, H - 60, "TAX INVOICE")
    c.setFont("Helvetica", 10)
    y = H - 90
    for line in [v.name, v.addr, f"GSTIN: {inv.vendor_gstin_shown}", "",
                 f"Invoice No: {inv.number}        Invoice Date: {_fmt_date(inv.date, 'dmy-')}",
                 f"Bill To: {BUYER_NAME}, Mumbai, Maharashtra", f"Buyer GSTIN: {BUYER_GSTIN}"]:
        c.drawString(40, y, line)
        y -= 15
    y -= 10
    c.setFont("Helvetica-Bold", 10)
    for x, t in [(40, "Description"), (260, "HSN"), (320, "Qty"), (370, "Rate"), (460, "Amount")]:
        c.drawString(x, y, t)
    c.setFont("Helvetica", 10)
    for ln in inv.lines:
        y -= 16
        for x, t in [(40, ln.desc), (260, ln.hsn), (320, str(ln.qty)), (370, f"{ln.rate:.2f}"),
                     (460, f"{ln.amount:,.2f}")]:
            c.drawString(x, y, t)
    y -= 30
    labels = {"sub": "Subtotal", "igst": "IGST", "cgst": "CGST", "sgst": "SGST",
              "total": "Grand Total (INR)"}
    for label, val in _tax_rows(inv, labels, False):
        c.drawString(340, y, label)
        c.drawRightString(530, y, val)
        y -= 16
    c.drawString(40, y - 20, "Payment terms: Net 30. Bank: HDFC Bank, A/c 50200012345678")


def render_band(c: canvas.Canvas, inv: Invoice) -> None:
    v = inv.vendor
    c.setFillColor(colors.HexColor("#1f3a5f"))
    c.rect(0, H - 90, W, 90, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, H - 45, v.name.upper())
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 62, v.addr)
    c.drawString(40, H - 76, f"GSTIN {inv.vendor_gstin_shown}")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 40, H - 125, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(W - 40, H - 142, f"Inv #: {inv.number}")
    c.drawRightString(W - 40, H - 156, f"Date: {_fmt_date(inv.date, 'long')}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, H - 125, "Billed to")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 140, BUYER_NAME)
    c.drawString(40, H - 154, BUYER_ADDR)
    c.drawString(40, H - 168, f"GSTIN: {BUYER_GSTIN}")
    y = H - 205
    c.setFillColor(colors.HexColor("#e8eef6"))
    c.rect(40, y - 5, W - 80, 20, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    for x, t in [(46, "Item"), (270, "HSN/SAC"), (340, "Qty"), (400, "Unit Price"), (490, "Total")]:
        c.drawString(x, y, t)
    c.setFont("Helvetica", 10)
    for ln in inv.lines:
        y -= 20
        for x, t in [(46, ln.desc), (270, ln.hsn), (340, str(ln.qty)), (400, f"{ln.rate:,.2f}"),
                     (490, f"{ln.amount:,.2f}")]:
            c.drawString(x, y, t)
    y -= 12
    c.line(40, y, W - 40, y)
    y -= 22
    labels = {"sub": "Sub Total", "igst": "IGST", "cgst": "CGST", "sgst": "SGST",
              "total": "Total Due"}
    for label, val in _tax_rows(inv, labels, False):
        bold = label == "Total Due"
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        c.drawString(360, y, label)
        c.drawRightString(W - 46, y, val)
        y -= 16
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(40, 60, "Thank you for your business. Payment within 45 days of invoice date.")


def render_boxed(c: canvas.Canvas, inv: Invoice) -> None:
    v = inv.vendor
    c.setLineWidth(0.8)
    c.rect(30, 30, W - 60, H - 60)
    c.setFont("Times-Bold", 22)
    c.drawCentredString(W / 2, H - 70, v.name)
    c.setFont("Times-Roman", 10)
    c.drawCentredString(W / 2, H - 86, v.addr)
    c.drawCentredString(W / 2, H - 100, f"GSTIN: {inv.vendor_gstin_shown}")
    c.line(30, H - 112, W - 30, H - 112)
    c.setFont("Times-Bold", 13)
    c.drawCentredString(W / 2, H - 132, "TAX INVOICE")
    c.setFont("Times-Roman", 10)
    c.drawString(40, H - 156, f"Bill No.: {inv.number}")
    c.drawRightString(W - 40, H - 156, f"Dated: {_fmt_date(inv.date, 'dmy/')}")
    c.drawString(40, H - 172, f"Customer: {BUYER_NAME}")
    c.drawString(40, H - 186, f"Customer GSTIN: {BUYER_GSTIN}")
    c.drawString(40, H - 200, f"Address: {BUYER_ADDR}")
    y = H - 230
    c.line(30, y + 14, W - 30, y + 14)
    c.setFont("Times-Bold", 10)
    for x, t in [(40, "Sr"), (70, "Particulars"), (300, "HSN"), (350, "Qty"), (400, "Rate"),
                 (480, "Taxable Value")]:
        c.drawString(x, y, t)
    c.line(30, y - 6, W - 30, y - 6)
    c.setFont("Times-Roman", 10)
    for i, ln in enumerate(inv.lines, 1):
        y -= 18
        for x, t in [(40, str(i)), (70, ln.desc), (300, ln.hsn), (350, str(ln.qty)),
                     (400, f"{ln.rate:.2f}"), (480, f"{ln.amount:.2f}")]:
            c.drawString(x, y, t)
    y -= 12
    c.line(30, y, W - 30, y)
    y -= 20
    labels = {"sub": "Total Taxable Value", "igst": "Integrated GST", "cgst": "Central GST",
              "sgst": "State GST", "total": "Invoice Total"}
    for label, val in _tax_rows(inv, labels, False):
        c.setFont("Times-Bold" if label == "Invoice Total" else "Times-Roman", 10)
        c.drawString(330, y, label)
        c.drawRightString(W - 40, y, val)
        y -= 16
    c.setFont("Times-Italic", 9)
    c.drawString(40, 50, "Subject to Mumbai jurisdiction. E. & O.E.")


def render_ledger(c: canvas.Canvas, inv: Invoice) -> None:
    v = inv.vendor
    c.setFont("Courier-Bold", 14)
    c.drawString(40, H - 50, v.name)
    c.setFont("Courier", 9)
    c.drawString(40, H - 64, v.addr)
    c.drawString(40, H - 76, f"GST No. {inv.vendor_gstin_shown}")
    c.setFont("Courier-Bold", 12)
    c.drawString(40, H - 104, "INVOICE")
    c.setFont("Courier", 9)
    c.drawString(40, H - 120, f"Invoice Number : {inv.number}")
    c.drawString(40, H - 132, f"Invoice Date   : {_fmt_date(inv.date, 'dmy-')}")
    c.drawString(40, H - 144, f"Sold To        : {BUYER_NAME}")
    c.drawString(40, H - 156, f"Buyer GST No.  : {BUYER_GSTIN}")
    y = H - 190
    c.setFillColor(colors.HexColor("#dddddd"))
    c.rect(40, y - 4, W - 80, 16, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Courier-Bold", 9)
    for x, t in [(44, "#"), (64, "Item Description"), (280, "HSN"), (335, "Qty"), (385, "Price"),
                 (470, "Line Total")]:
        c.drawString(x, y, t)
    c.setFont("Courier", 9)
    for i, ln in enumerate(inv.lines, 1):
        y -= 16
        for x, t in [(44, str(i)), (64, ln.desc), (280, ln.hsn), (335, str(ln.qty)),
                     (385, inr(ln.rate, True)), (470, inr(ln.amount, True))]:
            c.drawString(x, y, t)
    y -= 26
    labels = {"sub": "Net Amount", "igst": "IGST", "cgst": "CGST", "sgst": "SGST",
              "total": "Total Payable (Rs.)"}
    for label, val in _tax_rows(inv, labels, True):
        c.setFont("Courier-Bold" if "Payable" in label else "Courier", 9)
        c.drawString(320, y, label)
        c.drawRightString(W - 44, y, val)
        y -= 14
    c.setFont("Courier", 8)
    c.drawString(40, 50, "Declaration: goods supplied as per order. Interest @18% p.a. if late.")


RENDERERS = {"classic": render_classic, "band": render_band, "boxed": render_boxed,
             "ledger": render_ledger}


def render_letter(c: canvas.Canvas) -> None:
    v = V["kel"]
    c.setFont("Helvetica-Bold", 13)
    c.drawString(60, H - 70, v.name)
    c.setFont("Helvetica", 10)
    y = H - 90
    for line in [v.addr, "", "Date: 12 September 2026", "",
                 f"To: The Accounts Manager, {BUYER_NAME}", "",
                 "Subject: Change of bank account details", "", "Dear Sir/Madam,", "",
                 "We wish to inform you that, with effect from 1 October 2026, our bank account",
                 "for all payments has changed. Please update your records accordingly and",
                 "remit future payments to the new account shared by our finance team.",
                 "Our outstanding payments, if any, will be reconciled separately and we will",
                 "send a statement of account at the end of the month.", "",
                 "Kindly acknowledge receipt of this letter.", "", "Yours faithfully,", "",
                 "Authorised Signatory", v.name]:
        c.drawString(60, y, line)
        y -= 15


def render_memo(c: canvas.Canvas) -> None:
    c.setFont("Courier-Bold", 13)
    c.drawCentredString(W / 2, H - 70, "PETTY CASH MEMO")
    c.setFont("Courier", 9)
    c.drawCentredString(W / 2, H - 84, "Internal voucher - not a tax invoice")
    y = H - 120
    for line in ["Memo No   : PC-0382", "Date      : 18-09-2026", "Paid to   : Local courier boy",
                 "Purpose   : Courier charges - sample dispatch", "",
                 "Item                          Amount", "------------------------------------",
                 "Courier charges               Rs. 350.00",
                 "Tea / refreshments            Rs. 120.00",
                 "------------------------------------", "Total paid                    Rs. 470.00",
                 "", "Received with thanks", "", "Approved by: ________   Cashier: ________"]:
        c.drawString(70, y, line)
        y -= 14


# ----------------------------------------------------------------- scenarios

def corrupt_gstin(g: str, mode: str) -> str:
    if mode == "checksum":
        return g[:14] + ("A" if g[14] != "A" else "B")
    return g[:5] + "1" + g[6:]  # digit where PAN letter must be -> bad format


def build_set(seed: int) -> list[Invoice]:
    rng = random.Random(seed)
    seq = iter(range(1, 100))

    def mk(vkey: str, category: str, **kw) -> Invoice:
        inv = build_invoice(rng, V[vkey], next(seq) * 7, **kw)
        inv.category = category
        return inv

    invs: list[Invoice] = []
    clean_vendors = ["sgt", "sgt", "sgt", "kft", "kft", "spc", "spc",  # 7 intra-state
                     "bis", "dhp", "rom", "kel", "hlb"]  # 5 inter-state
    for k in clean_vendors:
        invs.append(mk(k, "clean"))

    # 3 wrong totals
    a = mk("bis", "wrong_total")
    a.total = round(a.total + 500.0, 2)
    a.note = "grand total overstated by 500.00"
    b = mk("kel", "wrong_total")
    digits = list(str(int(b.total)))
    i = next(k for k in range(len(digits) - 1, 0, -1) if digits[k] != digits[k - 1])
    digits[i], digits[i - 1] = digits[i - 1], digits[i]
    b.total = round(int("".join(digits)) + (b.total % 1), 2)
    b.note = "grand total has transposed digits"
    c3 = mk("spc", "wrong_total")
    c3.lines[0].amount = round(c3.lines[0].amount + 1200.0, 2)  # line shown wrong; subtotal stays
    c3.note = "a line amount does not equal qty x rate; lines do not sum to subtotal"
    invs += [a, b, c3]

    # 2 wrong tax math (grand total consistent with the wrong tax)
    t1 = mk("sgt", "wrong_tax", rate=18.0)
    half = round(t1.subtotal * 0.07, 2)
    t1.cgst = t1.sgst = half
    t1.tax_label_rate = 14.0
    t1.total = round(t1.subtotal + 2 * half, 2)
    t1.note = "CGST+SGST charged at 7%+7% (effective 14%, not a GST slab)"
    t2 = mk("kft", "wrong_tax")
    t2.igst, t2.cgst, t2.sgst = round(t2.cgst + t2.sgst, 2), 0.0, 0.0
    t2.tax_label_rate = 18.0
    t2.total = round(t2.subtotal + t2.igst, 2)
    t2.note = "intra-state supply charged IGST instead of CGST+SGST"
    invs += [t1, t2]

    # 2 invalid GSTIN
    g1 = mk("dhp", "invalid_gstin")
    g1.vendor_gstin_shown = corrupt_gstin(g1.vendor.gstin, "checksum")
    g1.note = "vendor GSTIN check character is wrong"
    g2 = mk("rom", "invalid_gstin")
    g2.vendor_gstin_shown = corrupt_gstin(g2.vendor.gstin, "format")
    g2.note = "vendor GSTIN does not match the 15-character format"
    invs += [g1, g2]

    # 2 high amounts for the vendor (about 10x typical)
    h1 = mk("kft", "high_amount", target=V["kft"].typical * 10)
    h1.note = "amount about 10x the vendor's typical invoice"
    h2 = mk("rom", "high_amount", target=V["rom"].typical * 12)
    h2.note = "amount about 12x the vendor's typical invoice"
    invs += [h1, h2]

    rng.shuffle(invs)
    # 2 exact duplicates (same vendor, number, date, amounts) re-rendered in another layout
    originals = [i for i in invs if i.category == "clean"]
    for orig in (originals[0], next(i for i in originals if i.vendor.state != "27")):
        dup = Invoice(**{**orig.__dict__})
        dup.category = "duplicate"
        dup.template = next(t for t in RENDERERS if t != orig.template)
        dup.note = f"re-sent copy of {orig.number}"
        pos = rng.randint(invs.index(orig) + 1, len(invs))
        invs.insert(pos, dup)
    return invs


# ----------------------------------------------------------------- scanning

def scan_effect(pdf_path: Path, rng: np.random.Generator, dpi: int = 170) -> Image.Image:
    with fitz.open(pdf_path) as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    img = img.rotate(float(rng.uniform(-1.4, 1.4)), expand=True, fillcolor=250,
                     resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(float(rng.uniform(0.6, 0.9))))
    arr = np.asarray(img, dtype=np.float32)
    arr = arr * rng.uniform(0.88, 0.97) + rng.uniform(0, 8)  # dim, uneven-ish scanner light
    gradient = np.linspace(0, rng.uniform(5, 18), arr.shape[1])[None, :]
    arr = arr - gradient
    arr += rng.normal(0, 13, arr.shape)
    specks = rng.random(arr.shape) < 0.0008
    arr[specks] = rng.uniform(40, 120)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ----------------------------------------------------------------- main

def truth_for(inv: Invoice, name: str) -> dict:
    cat = inv.category
    reasons = {
        "clean": ("approve", "ok", "All checks passed"),
        "wrong_total": ("manual_review", "totals_mismatch",
                        "Totals/tax failed: " + inv.note),
        "wrong_tax": ("manual_review", "tax_invalid", "Totals/tax failed: " + inv.note),
        "invalid_gstin": ("manual_review", "invalid_gstin", "Vendor GSTIN failed: " + inv.note),
        "duplicate": ("reject", "duplicate", "Duplicate of an earlier invoice (same vendor, "
                                              "number and total)"),
        "high_amount": ("manual_review", "amount_anomaly",
                        "Anomalous amount for this vendor: " + inv.note),
    }
    decision, code, reason = reasons[cat]
    tax = round(inv.cgst + inv.sgst + inv.igst, 2)
    return {
        "category": cat, "is_invoice": True, "template": inv.template,
        "expected": {
            "vendor": inv.vendor.name, "vendor_gstin": inv.vendor_gstin_shown,
            "buyer_gstin": BUYER_GSTIN, "invoice_no": inv.number,
            "date": inv.date.isoformat(), "subtotal": inv.subtotal, "cgst": inv.cgst,
            "sgst": inv.sgst, "igst": inv.igst, "tax": tax, "total": inv.total,
            "line_items": [{"description": ln.desc, "hsn": ln.hsn, "quantity": ln.qty,
                            "unit_price": ln.rate, "amount": ln.amount} for ln in inv.lines],
        },
        "tax_type": "IGST" if inv.igst and not (inv.cgst or inv.sgst) else "CGST+SGST",
        "decision": decision, "reason_code": code, "reason": reason, "note": inv.note,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/invoices")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    out = Path(a.out)
    (out / "scanned").mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    nprng = np.random.default_rng(a.seed)

    invs = build_set(a.seed)
    truth: dict[str, dict] = {}
    names: list[str] = []
    n = 0

    def write(draw, name: str) -> Path:
        path = out / name
        c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
        draw(c)
        c.save()
        return path

    non_invoice_slots = sorted(rng.sample(range(len(invs) + 2), 2))
    non_invoice_iter = iter([("letter", render_letter), ("memo", render_memo)])
    sequence: list = []
    for inv in invs:
        while non_invoice_slots and len(sequence) == non_invoice_slots[0]:
            non_invoice_slots.pop(0)
            sequence.append(next(non_invoice_iter))
        sequence.append(inv)
    sequence += list(non_invoice_iter)

    for item in sequence:
        n += 1
        name = f"inv_{n:02d}.pdf"
        if isinstance(item, Invoice):
            write(lambda c, inv=item: RENDERERS[inv.template](c, inv), name)
            truth[name] = truth_for(item, name)
            if item.category == "duplicate":
                orig_name = next(k for k, t in truth.items()
                                 if t["expected"]["invoice_no"] == item.number
                                 and t["category"] == "clean")
                truth[name]["duplicate_of"] = orig_name
        else:
            kind, fn = item
            write(fn, name)
            truth[name] = {
                "category": "non_invoice", "is_invoice": False, "template": kind,
                "expected": {k: None for k in ("vendor", "vendor_gstin", "buyer_gstin",
                                               "invoice_no", "date", "subtotal", "cgst", "sgst",
                                               "igst", "tax", "total")} | {"line_items": []},
                "tax_type": None, "decision": "reject", "reason_code": "not_invoice",
                "reason": "Not an invoice: " + ("bank-detail change letter" if kind == "letter"
                                                else "internal petty cash memo"),
                "note": kind,
            }
        names.append(name)

    # scanned-style versions: 4 clean, wrong_total, invalid_gstin, high_amount, non-invoice
    wanted = {"clean": 4, "wrong_total": 1, "invalid_gstin": 1, "high_amount": 1,
              "non_invoice": 1}
    picked: list[str] = []
    for nm in names:
        cat = truth[nm]["category"]
        if wanted.get(cat, 0) > 0:
            wanted[cat] -= 1
            picked.append(nm)
    for k, nm in enumerate(picked):
        ext = "png" if k % 2 == 0 else "jpg"
        img = scan_effect(out / nm, nprng)
        sname = f"scanned/{Path(nm).stem}_scan.{ext}"
        if ext == "png":
            img.save(out / sname)
        else:
            img.save(out / sname, quality=60)
        truth[sname] = {**truth[nm], "scan_of": nm}
        truth[sname].pop("duplicate_of", None)

    counts: dict[str, int] = {}
    for t in truth.values():
        if "scan_of" not in t:
            counts[t["category"]] = counts.get(t["category"], 0) + 1
    gt = {
        "meta": {
            "seed": a.seed, "buyer": {"name": BUYER_NAME, "gstin": BUYER_GSTIN},
            "digital_files": sum(1 for k in truth if "scan_of" not in truth[k]),
            "scanned_files": sum(1 for k in truth if "scan_of" in truth[k]),
            "categories": counts,
            "note": "Process the digital set and the scanned set against separate fresh DBs: "
                    "a scanned copy has the same duplicate hash as its original.",
        },
        "vendors": [{"gstin": v.gstin, "name": v.name, "state": v.state} for v in VENDORS],
        "files": truth,
    }
    (out / "ground_truth.json").write_text(json.dumps(gt, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    print(f"Wrote {gt['meta']['digital_files']} PDFs + {gt['meta']['scanned_files']} scans "
          f"to {out} ; categories {counts}")


if __name__ == "__main__":
    main()
