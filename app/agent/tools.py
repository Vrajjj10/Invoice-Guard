"""Agent tools: specs for the LLM + deterministic implementations."""

import difflib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Job, JobStatus
from app.llm.provider import ToolSpec
from app.llm.schema import InvoiceFields
from app.validators import duplicate_hash, validate_gstin, validate_totals
from app.validators.gstin import normalize_gstin

NAME_MATCH_RATIO = 0.85
_EMPTY = {"type": "object", "properties": {}}

TOOL_SPECS = [
    ToolSpec("check_duplicate",
             "Check if this invoice (vendor + number + amount) was already received.", _EMPTY),
    ToolSpec("validate_totals",
             "Verify line items vs subtotal, GST split/rate and grand total.", _EMPTY),
    ToolSpec("validate_gstin", "Validate a GSTIN's format and checksum.", {
        "type": "object",
        "properties": {"party": {"type": "string", "enum": ["vendor", "buyer"]}},
        "required": ["party"],
    }),
    ToolSpec("lookup_vendor", "Find the vendor in the vendor master by GSTIN or name.", {
        "type": "object",
        "properties": {"gstin": {"type": "string"}, "name": {"type": "string"}},
    }),
    ToolSpec("flag_anomaly",
             "Score the invoice amount against vendor history for anomalies.", _EMPTY),
]


@dataclass
class ToolContext:
    fields: InvoiceFields
    db: Session | None = None
    job_id: str | None = None


def _norm_name(s: str | None) -> str:
    s = re.sub(r"\b(PVT|PRIVATE|LTD|LIMITED|LLP|CO|INC)\b\.?", "", (s or "").upper())
    return re.sub(r"[^A-Z0-9]", "", s)


def _ratio(a: str | None, b: str | None) -> float:
    return difflib.SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio()


@lru_cache
def load_vendors(path: str | None = None) -> tuple[dict, ...]:
    """Vendor master from JSON (cached)."""
    p = Path(path or get_settings().vendors_path)
    return tuple(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else ()


def find_vendor(gstin: str | None, name: str | None, vendors: tuple[dict, ...]) -> dict:
    """Match by exact GSTIN, else by fuzzy name."""
    g = normalize_gstin(gstin)
    for v in vendors:
        if g and v["gstin"] == g:
            return {"found": True, "match": "gstin", "vendor": v,
                    "name_matches": not name or _ratio(name, v["name"]) >= NAME_MATCH_RATIO}
    if _norm_name(name):
        best = max(vendors, default=None, key=lambda v: _ratio(name, v["name"]))
        if best and _ratio(name, best["name"]) >= NAME_MATCH_RATIO:
            return {"found": True, "match": "name", "vendor": best,
                    "gstin_matches": not g or best["gstin"] == g}
    return {"found": False, "match": None, "vendor": None}


def check_duplicate(ctx: ToolContext) -> dict:
    """Look for other non-failed jobs with the same duplicate hash."""
    f = ctx.fields
    h = duplicate_hash(f.vendor_gstin, f.vendor_name, f.invoice_number, f.grand_total)
    if h is None:
        return {"ok": None, "duplicate": None, "detail": "vendor, number or amount missing"}
    matches: list[str] = []
    if ctx.db is not None:
        q = select(Job.id).where(Job.dup_hash == h, Job.status != JobStatus.FAILED)
        if ctx.job_id:
            q = q.where(Job.id != ctx.job_id)
        matches = list(ctx.db.scalars(q))
    return {"ok": not matches, "duplicate": bool(matches), "hash": h, "matching_jobs": matches}


def run_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    """Dispatch a tool call; errors are returned, not raised."""
    f = ctx.fields
    try:
        if name == "check_duplicate":
            return check_duplicate(ctx)
        if name == "validate_totals":
            return validate_totals(f)
        if name == "validate_gstin":
            party = args.get("party", "vendor")
            gstin = f.buyer_gstin if party == "buyer" else f.vendor_gstin
            return {"party": party, **validate_gstin(gstin)}
        if name == "lookup_vendor":
            return find_vendor(args.get("gstin") or f.vendor_gstin,
                               args.get("name") or f.vendor_name, load_vendors())
        if name == "flag_anomaly":
            return {"ok": None, "anomaly": None, "detail": "not implemented (Phase 5)"}
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # surface to the model instead of crashing the loop
        return {"error": f"{type(exc).__name__}: {exc}"}
