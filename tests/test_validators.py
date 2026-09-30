import pytest

from app.llm.schema import InvoiceFields, LineItem
from app.validators import duplicate_hash, validate_gstin, validate_totals
from app.validators.gstin import gstin_check_char
from app.validators.totals import (
    check_grand_total,
    check_line_items,
    check_tax_rate,
    check_tax_split,
    is_intra_state,
)

VENDOR = "27AAPFU0939F1ZV"  # Maharashtra
BUYER_MH = "27AAACD1234E1Z9"
BUYER_KA = "29AAGCB7383J1Z4"


def _inv(**kw) -> InvoiceFields:
    base = dict(
        is_invoice=True, confidence=0.9, vendor_name="Shree Ganesh Traders Pvt Ltd",
        vendor_gstin=VENDOR, buyer_gstin=BUYER_MH, invoice_number="SGT/2026/0457",
        line_items=[
            LineItem(description="Bolts", quantity=500, unit_price=4.5, amount=2250),
            LineItem(description="Hose", quantity=20, unit_price=350, amount=7000),
            LineItem(description="Bearing", quantity=40, unit_price=95, amount=3800),
        ],
        subtotal=13050, cgst=1174.5, sgst=1174.5, igst=None, grand_total=15399,
    )
    return InvoiceFields(**{**base, **kw})


# --- GSTIN ---

@pytest.mark.parametrize("g", [VENDOR, BUYER_MH, BUYER_KA, "27aapfu0939f1zv", "27 AAPFU 0939F1ZV"])
def test_valid_gstins(g):
    assert validate_gstin(g)["ok"] is True


@pytest.mark.parametrize("g,detail", [
    ("27AAPFU0939F1ZX", "checksum"),
    ("27AAPFU0939F1Z", "bad format"),
    ("27AAPFU0939F0ZV", "bad format"),  # 13th char cannot be 0
    ("27AAPFU0939F1YV", "bad format"),  # 14th char must be Z
    ("00AAPFU0939F1ZV", "state"),
])
def test_invalid_gstins(g, detail):
    r = validate_gstin(g)
    assert r["ok"] is False and detail in r["detail"]


def test_missing_gstin_is_skipped():
    assert validate_gstin(None)["ok"] is None and validate_gstin("")["ok"] is None


def test_check_char():
    assert gstin_check_char(VENDOR[:14]) == "V"


# --- totals ---

def test_clean_invoice_passes():
    r = validate_totals(_inv())
    assert r["ok"] is True, r


def test_line_amount_mismatch():
    items = [LineItem(description="x", quantity=2, unit_price=10, amount=25)]
    out = check_line_items(_inv(line_items=items, subtotal=25))
    assert out[0]["check"] == "line_1" and out[0]["ok"] is False


def test_line_sum_vs_subtotal():
    out = check_line_items(_inv(subtotal=14000))
    assert out[-1]["check"] == "line_sum" and out[-1]["ok"] is False


def test_line_sum_rounding_tolerated():
    assert check_line_items(_inv(subtotal=13050.6))[-1]["ok"] is True


def test_line_sum_skipped_when_amount_missing():
    items = [LineItem(description="x", amount=10), LineItem(description="y")]
    assert check_line_items(_inv(line_items=items, subtotal=10))[-1]["ok"] is None


def test_grand_total_mismatch():
    assert check_grand_total(_inv(grand_total=16000))["ok"] is False


def test_grand_total_missing():
    assert check_grand_total(_inv(grand_total=None))["ok"] is None


def test_non_standard_rate():
    assert check_tax_rate(_inv(cgst=1000, sgst=1000, grand_total=15050))["ok"] is False


def test_zero_rated_ok():
    assert check_tax_rate(_inv(cgst=0, sgst=0, grand_total=13050))["ok"] is True


# --- CGST/SGST vs IGST ---

def test_intra_state_detection():
    assert is_intra_state(VENDOR, BUYER_MH) is True
    assert is_intra_state(VENDOR, BUYER_KA) is False
    assert is_intra_state(VENDOR, None) is None


def test_intra_state_with_igst_fails():
    assert check_tax_split(_inv(cgst=None, sgst=None, igst=2349))["ok"] is False


def test_intra_state_unequal_cgst_sgst_fails():
    assert check_tax_split(_inv(cgst=1000, sgst=1349))["ok"] is False


def test_inter_state_igst_ok():
    f = _inv(buyer_gstin=BUYER_KA, cgst=None, sgst=None, igst=2349)
    assert check_tax_split(f)["ok"] is True and validate_totals(f)["ok"] is True


def test_inter_state_with_cgst_fails():
    assert check_tax_split(_inv(buyer_gstin=BUYER_KA))["ok"] is False


def test_unknown_state_mixed_taxes_fails():
    assert check_tax_split(_inv(buyer_gstin=None, igst=10))["ok"] is False


def test_unknown_state_skipped():
    assert check_tax_split(_inv(buyer_gstin=None))["ok"] is None


def test_overall_fails_if_any_check_fails():
    assert validate_totals(_inv(grand_total=1))["ok"] is False


def test_overall_none_if_nothing_checkable():
    f = InvoiceFields(is_invoice=True, confidence=0.5)
    assert validate_totals(f)["ok"] is None


# --- duplicate hash ---

def test_dup_hash_normalizes():
    a = duplicate_hash(VENDOR, "X", "SGT/2026/0457", 15399)
    b = duplicate_hash("27aapfu0939f1zv", "Other", "sgt-2026-0457", 15399.0)
    assert a == b and len(a) == 64


def test_dup_hash_differs_on_amount_or_number():
    a = duplicate_hash(VENDOR, None, "INV-1", 100)
    assert a != duplicate_hash(VENDOR, None, "INV-1", 100.01)
    assert a != duplicate_hash(VENDOR, None, "INV-2", 100)


def test_dup_hash_falls_back_to_name():
    assert duplicate_hash(None, "Shree Ganesh", "INV-1", 1) == duplicate_hash(
        None, "SHREE  GANESH", "INV-1", 1
    )


def test_dup_hash_incomplete_is_none():
    assert duplicate_hash(VENDOR, None, None, 100) is None
    assert duplicate_hash(None, None, "INV-1", 100) is None
    assert duplicate_hash(VENDOR, None, "INV-1", None) is None
