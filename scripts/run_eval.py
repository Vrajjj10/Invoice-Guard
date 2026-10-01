"""Evaluation harness: run every file in data/invoices through the real pipeline
(`app.pipeline.process_job`) and score it against data/invoices/ground_truth.json.

    python scripts/run_eval.py                          # default model
    python scripts/run_eval.py --model gemini-3.5-flash # escalation model, same files
    python scripts/run_eval.py --limit 5 --delay 6
    python scripts/run_eval.py --scanned-only           # re-run only scans (after OCR changes)
    python scripts/run_eval.py --report-only            # rebuild EVAL.md from eval_results.json

Gemini calls are throttled (--delay), retried with exponential backoff on 429/5xx, and cached on
disk by sha256(file) + model (+ OCR settings when the text came from OCR), so re-runs spend no
quota. Only the SAP HTTP post is stubbed.
Digital and scanned sets run on separate fresh DBs (a scan duplicates its original).
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

EVAL_DIR = ROOT / "data" / "eval"
os.environ["DATABASE_URL"] = f"sqlite:///{EVAL_DIR / 'eval.db'}"  # before importing app
os.environ["UPLOAD_DIR"] = str(EVAL_DIR / "uploads")
EVAL_DIR.mkdir(parents=True, exist_ok=True)

from app import pipeline  # noqa: E402
from app.agent import loop as agent_loop  # noqa: E402
from app.agent.tools import _ratio  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.extraction.ocr import load_engine  # noqa: E402
from app.extraction.text import RENDER_DPI, detect_file_type  # noqa: E402
from app.llm import client as llm_client  # noqa: E402
from app.llm import provider as llm_provider  # noqa: E402
from app.llm.client import LLMResult  # noqa: E402
from app.llm.provider import ChatTurn  # noqa: E402
from app.llm.schema import InvoiceFields  # noqa: E402

DATA = ROOT / "data" / "invoices"
CACHE_DIR = EVAL_DIR / "cache"
RESULTS = ROOT / "eval_results.json"
REPORT = ROOT / "EVAL.md"
DECISIONS = ["approve", "manual_review", "reject"]

# ASSUMED blended USD per 1M tokens (Gemini reports only a total count here, no in/out split).
# Placeholder estimates, not verified list prices; override with --price.
DEFAULT_PRICE = {"gemini-3.5-flash-lite": 0.25, "gemini-3.5-flash": 1.00}

FIELDS = ["vendor_name", "vendor_gstin", "buyer_gstin", "invoice_number", "invoice_date",
          "subtotal", "cgst", "sgst", "igst", "grand_total", "line_items"]


# ---------------------------------------------------------------- throttle / retry / cache

class Throttle:
    def __init__(self, delay: float, retries: int):
        self.delay, self.retries = delay, retries
        self.last, self.api_calls, self.retried = 0.0, 0, 0
        self.slept = 0.0  # seconds spent waiting (throttle + backoff), excluded from latency

    def call(self, fn, *a, **kw):
        for attempt in range(self.retries + 1):
            wait = self.last + self.delay - time.monotonic()
            if wait > 0:
                self.slept += wait
                time.sleep(wait)
            self.last = time.monotonic()
            self.api_calls += 1
            try:
                return fn(*a, **kw)
            except Exception as exc:
                if not self.transient(exc) or attempt == self.retries:
                    raise
                self.retried += 1
                backoff = min(60.0, 5.0 * 2 ** attempt)
                print(f"    transient error ({str(exc)[:80]}); retry in {backoff:.0f}s", flush=True)
                self.slept += backoff
                time.sleep(backoff)

    @staticmethod
    def transient(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if code in (429, 500, 502, 503, 504):
            return True
        return bool(re.search(r"\b(429|500|502|503|504)\b|RESOURCE_EXHAUSTED|UNAVAILABLE|"
                              r"timed? ?out", str(exc), re.I))


class NoToolSession:
    """Cache-hit stand-in for the agent LLM: calls no tools, so the deterministic backstop runs
    all six checks (the tools take no model-supplied numbers, so verdicts are identical)."""

    def send(self, message):
        return ChatTurn(text="(cached)", calls=[], tokens=0)


class NoToolProvider:
    def start(self, system, tools):
        return NoToolSession()


class Cache:
    """One JSON file per (file hash, model) holding the LLM and agent sections."""

    current_hash = ""
    model = ""
    ocr_tag = ""  # set per file once we know OCR produced the text

    @classmethod
    def path(cls) -> Path:
        return CACHE_DIR / f"{cls.current_hash}_{cls.model}{cls.ocr_tag}.json"

    @classmethod
    def load(cls) -> dict:
        p = cls.path()
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    @classmethod
    def save(cls, section: str, value: dict) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = cls.load()
        data[section] = value
        cls.path().write_text(json.dumps(data), encoding="utf-8")


TH: Throttle | None = None
STATS = {"cache_hits": 0, "cache_misses": 0}


def ocr_settings() -> dict:
    return {"max_side_px": get_settings().ocr_max_side_len, "pdf_dpi": RENDER_DPI}


def cached_extract(text: str, ocr_confidence: float | None = None) -> LLMResult:
    # OCR output (hence the LLM input) depends on the OCR settings; text PDFs don't
    o = ocr_settings()
    used_ocr = ocr_confidence is not None and ocr_confidence < 1.0
    Cache.ocr_tag = f"_ocr{o['max_side_px']}_{o['pdf_dpi']}" if used_ocr else ""
    hit = Cache.load().get("llm")
    if hit:
        STATS["cache_hits"] += 1
        return LLMResult(InvoiceFields.model_validate(hit["fields"]), hit["model"],
                         hit["escalated"], hit["tokens"], hit["elapsed_ms"])
    STATS["cache_misses"] += 1
    slept0 = TH.slept
    res = llm_client.extract_invoice(text, ocr_confidence)  # exceptions -> failed job, not cached
    res.elapsed_ms = max(0, res.elapsed_ms - int((TH.slept - slept0) * 1000))
    Cache.save("llm", {"fields": res.fields.model_dump(), "model": res.model,
                       "escalated": res.escalated, "tokens": res.tokens,
                       "elapsed_ms": res.elapsed_ms})
    return res


def cached_agent(ctx):
    hit = Cache.load().get("agent")
    if hit:
        res = agent_loop.run_agent(ctx, NoToolProvider())
        res.tokens, res.iterations, res.notes = hit["tokens"], hit["iterations"], hit["notes"]
        res.agent_ms = hit["elapsed_ms"]
        return res
    start, slept0 = time.perf_counter(), TH.slept
    res = agent_loop.run_agent(ctx)
    res.agent_ms = max(0, int((time.perf_counter() - start - (TH.slept - slept0)) * 1000))
    if res.error is None:  # don't cache a degraded (backfilled-after-LLM-failure) run
        Cache.save("agent", {"tokens": res.tokens, "iterations": res.iterations,
                             "notes": res.notes, "elapsed_ms": res.agent_ms})
    return res


# ---------------------------------------------------------------- scoring

def _num(a, b, tol=0.011) -> bool:
    return abs((a or 0.0) - (b or 0.0)) <= tol


def _alnum(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def field_correct(name: str, exp: dict, got: InvoiceFields) -> bool | None:
    """True/False, or None when the ground truth has no value for this field."""
    key = {"vendor_name": "vendor", "invoice_number": "invoice_no", "invoice_date": "date",
           "grand_total": "total"}.get(name, name)
    e = exp.get(key)
    if name == "line_items":
        if not e:
            return None
        items = got.line_items
        return len(items) == len(e) and all(
            _num(g.amount, x["amount"]) and _num(g.quantity, x["quantity"], 1e-6)
            for g, x in zip(items, e, strict=True))
    if name in ("cgst", "sgst", "igst"):
        return _num(getattr(got, name), e)  # None == 0.0 on an invoice
    if e is None:
        return None
    v = getattr(got, name)
    if name == "vendor_name":
        return v is not None and _ratio(v, e) >= 0.85
    if name in ("vendor_gstin", "buyer_gstin", "invoice_number"):
        return _alnum(v) == _alnum(e)
    if name == "invoice_date":
        return v == e
    return v is not None and _num(v, e)


def key_token_recall(text: str, exp: dict) -> float | None:
    """Share of the invoice no., vendor GSTIN and grand total that appear verbatim in the text."""
    if not exp.get("invoice_no"):
        return None
    flat = re.sub(r"\s+", "", text).upper()
    total = exp["total"]
    total_forms = {f"{total:,.2f}", f"{total:.2f}"}
    hits = [_alnum(exp["invoice_no"]) in re.sub(r"[^A-Z0-9]", "", flat),
            exp["vendor_gstin"] in flat,
            any(t in flat for t in total_forms)]
    return sum(hits) / 3


def pct(n, d) -> float | None:
    return None if not d else round(100 * n / d, 1)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 4) if xs else None


def p95(xs):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * 0.95
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


# ---------------------------------------------------------------- run

def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def run_file(rel: str, truth: dict) -> dict:
    path = DATA / rel
    data = path.read_bytes()
    fhash = hashlib.sha256(data).hexdigest()
    Cache.current_hash = fhash
    Cache.ocr_tag = ""
    db = SessionLocal()
    job = models.Job(file_hash=fhash, filename=rel, file_type=detect_file_type(data),
                     stored_path=str(path))
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    t0 = time.perf_counter()
    pipeline.process_job(job_id)
    wall_ms = int((time.perf_counter() - t0) * 1000)

    db = SessionLocal()
    job = db.get(models.Job, job_id)
    audit = (db.query(models.AuditLog).filter_by(job_id=job_id)
             .order_by(models.AuditLog.id).all())
    reasons = json.loads(audit[-1].reasons) if audit else []
    db.close()

    exp = truth["expected"]
    fields = InvoiceFields.model_validate_json(job.fields_json) if job.fields_json else None
    correct = {}
    if fields is not None and truth["is_invoice"]:
        for f in FIELDS:
            r = field_correct(f, exp, fields)
            if r is not None:
                correct[f] = r
    llm_ms = job.llm_ms or 0
    return {
        "file": rel, "category": truth["category"], "scanned": "scan_of" in truth,
        "expected_decision": truth["decision"], "actual_decision": job.decision,
        "status": job.status, "error": job.error, "reasons": reasons,
        "expected_is_invoice": truth["is_invoice"],
        "actual_is_invoice": fields.is_invoice if fields else None,
        "extraction_method": job.extraction_method, "ocr_confidence": job.ocr_confidence,
        "text_chars": len(job.extracted_text or ""),
        "key_token_recall": key_token_recall(job.extracted_text or "", exp),
        "llm_confidence": job.llm_confidence, "llm_model": job.llm_model,
        "escalated": job.llm_escalated, "field_correct": correct,
        "extraction_ms": job.extraction_ms or 0, "llm_ms": llm_ms,
        "agent_ms": AGENT_MS.get(job_id, 0),
        "latency_ms": (job.extraction_ms or 0) + llm_ms + AGENT_MS.get(job_id, 0),
        "wall_ms": wall_ms,
        "tokens_llm": job.llm_tokens or 0, "tokens_agent": job.agent_tokens or 0,
        "tokens": (job.llm_tokens or 0) + (job.agent_tokens or 0),
        "agent_backfilled": json.loads(job.checks_json or "{}").get("backfilled", [])
        if job.checks_json else [],
    }


AGENT_MS: dict[str, int] = {}


def metrics(rows: list[dict], model: str, price: float) -> dict:
    ok = [r for r in rows if r["status"] == "done"]
    inv = [r for r in rows if r["expected_is_invoice"]]
    ocr_text = [r for r in rows if r["text_chars"] >= 50]

    def ocr_block(sub):
        return {"files": len(sub),
                "text_extracted": len([r for r in sub if r["text_chars"] >= 50]),
                "avg_ocr_confidence": mean([r["ocr_confidence"] for r in sub]),
                "avg_key_token_recall": mean([r["key_token_recall"] for r in sub])}

    digital = [r for r in rows if r["extraction_method"] == "text"]
    scanned = [r for r in rows if r["extraction_method"] in ("ocr", "mixed")]

    per_field = {}
    for f in FIELDS:
        vals = [r["field_correct"][f] for r in rows if f in r["field_correct"]]
        per_field[f] = {"correct": sum(vals), "total": len(vals), "pct": pct(sum(vals), len(vals))}
    tot_c = sum(v["correct"] for v in per_field.values())
    tot_n = sum(v["total"] for v in per_field.values())

    cm = {e: {a: 0 for a in [*DECISIONS, "error"]} for e in DECISIONS}
    for r in rows:
        cm[r["expected_decision"]][r["actual_decision"] if r["status"] == "done"
                                    and r["actual_decision"] else "error"] += 1
    exp_flag = [r for r in rows if r["expected_decision"] != "approve"]
    # a failed job counts as not approved
    pred_flag = [r for r in rows if r["actual_decision"] != "approve"]
    tp = len([r for r in exp_flag if r["actual_decision"] != "approve"])
    false_approve = [r["file"] for r in exp_flag if r["actual_decision"] == "approve"]
    false_flag = [r["file"] for r in rows if r["expected_decision"] == "approve"
                  and r["actual_decision"] != "approve"]
    lat = [r["latency_ms"] for r in ok]
    toks = [r["tokens"] for r in rows]
    cost_paid = [t * price / 1e6 for t in toks]
    dec_ok = len([r for r in rows if r["actual_decision"] == r["expected_decision"]])
    return {
        "model": model, "files": len(rows), "jobs_failed": len(rows) - len(ok),
        "escalated_files": len([r for r in rows if r["escalated"]]),
        "ocr": {"overall": ocr_block(rows), "digital": ocr_block(digital),
                "scanned": ocr_block(scanned), "text_extraction_success_pct":
                pct(len(ocr_text), len(rows))},
        "is_invoice_accuracy_pct": pct(len([r for r in inv if r["actual_is_invoice"]]) +
                                       len([r for r in rows if not r["expected_is_invoice"]
                                            and r["actual_is_invoice"] is False]), len(rows)),
        "field_accuracy": {"per_field": per_field, "overall_pct": pct(tot_c, tot_n),
                           "fields_correct": tot_c, "fields_total": tot_n},
        "decision_accuracy_pct": pct(dec_ok, len(rows)), "decisions_correct": dec_ok,
        "confusion_matrix": cm,
        "flagged_class": {
            "expected_flagged": len(exp_flag), "predicted_flagged": len(pred_flag),
            "precision_pct": pct(tp, len(pred_flag)), "recall_pct": pct(tp, len(exp_flag)),
            "false_approves": len(false_approve), "false_approve_files": false_approve,
            "false_flags": len(false_flag), "false_flag_files": false_flag},
        "latency_ms": {"avg": round(statistics.fmean(lat)) if lat else None, "p95": p95(lat)},
        "tokens_per_invoice": {"avg": round(statistics.fmean(toks)) if toks else None,
                               "total": sum(toks)},
        "cost_per_invoice_usd": {"free_tier": 0.0, "paid_estimate":
                                 round(statistics.fmean(cost_paid), 6) if cost_paid else None,
                                 "assumed_usd_per_mtok": price},
    }


def run_eval(args) -> None:
    s = get_settings()
    model = args.model or s.default_model
    s.default_model = model  # pipeline extraction + agent both read this
    Cache.model = model
    price = args.price if args.price is not None else DEFAULT_PRICE.get(model, 0.5)

    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))["files"]
    digital = sorted(k for k, v in truth.items() if "scan_of" not in v)
    scanned = sorted(k for k, v in truth.items() if "scan_of" in v)
    order = scanned if args.scanned_only else (digital + scanned)
    order = order[: args.limit or None]

    global TH
    th = TH = Throttle(args.delay, args.retries)
    llm_client._call = lambda m, t, _o=llm_client._call: th.call(_o, m, t)
    _send = llm_provider.GeminiSession.send
    llm_provider.GeminiSession.send = lambda self, msg: th.call(_send, self, msg)
    pipeline.extract_invoice = cached_extract
    pipeline.run_agent = _tracking_agent
    from app.sap import posting
    posting.post_with_retry = lambda payload: "51" + str(abs(hash(json.dumps(payload))))[:8]

    load_engine()
    print(f"model={model} files={len(order)} delay={args.delay}s", flush=True)
    rows, current_set = [], None
    for i, rel in enumerate(order, 1):
        which = "scanned" if rel in scanned else "digital"
        if which != current_set:
            reset_db()
            current_set = which
        row = run_file(rel, truth[rel])
        rows.append(row)
        flag = "ok " if row["actual_decision"] == row["expected_decision"] else "MISS"
        print(f"[{i}/{len(order)}] {flag} {rel}: expected={row['expected_decision']} "
              f"got={row['actual_decision']} ({row['latency_ms']} ms, {row['tokens']} tok)",
              flush=True)

    store = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {"runs": {}}
    prev = store["runs"].get(model)
    if args.scanned_only and prev:  # digital files don't use OCR: keep their rows, swap the scans
        rows = [r for r in prev["files"] if r["file"] not in scanned] + rows
    run = {"model": model, "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
           "limit": args.limit, "delay_s": args.delay,
           "cache_hits": STATS["cache_hits"], "cache_misses": STATS["cache_misses"],
           "ocr_settings": ocr_settings(),
           "scanned_only_rerun": bool(args.scanned_only),
           "api_calls": th.api_calls, "api_retries": th.retried,
           "metrics": metrics(rows, model, price), "files": rows}
    store["runs"][model] = run
    if not args.model or "default_model" not in store:
        store["default_model"] = model
    RESULTS.write_text(json.dumps(store, indent=2), encoding="utf-8")
    write_report(store)
    print(f"wrote {RESULTS.name}, {REPORT.name}")


def _tracking_agent(ctx):
    res = cached_agent(ctx)
    AGENT_MS[ctx.job_id] = res.agent_ms
    return res


# ---------------------------------------------------------------- report

def _fmt(v, suffix=""):
    return "n/a" if v is None else f"{v}{suffix}"


def likely_reason(r: dict) -> str:
    if r["status"] != "done":
        return f"Pipeline error: {r['error']}"
    bad = [f for f, ok in r["field_correct"].items() if not ok]
    wrong_dec = r["actual_decision"] != r["expected_decision"]
    ocr = r["extraction_method"] in ("ocr", "mixed")
    if wrong_dec and bad:
        src = "OCR misread" if ocr and (r["key_token_recall"] or 1) < 1 else "LLM extraction error"
        return f"{src} in {', '.join(bad)} changed the checks' outcome"
    if wrong_dec and r["actual_is_invoice"] != r["expected_is_invoice"]:
        return "LLM misclassified invoice vs non-invoice"
    if wrong_dec:
        return "Extraction correct; router/validator rule gap or low LLM confidence"
    if bad:
        src = "OCR misread" if ocr and (r["key_token_recall"] or 1) < 1 else "LLM extraction error"
        return f"{src} in {', '.join(bad)} (decision still correct)"
    return ""


def write_report(store: dict) -> None:
    runs = dict(store["runs"])
    base_model = store.get("default_model") or next(iter(runs))
    models_ = [base_model] + [m for m in runs if m != base_model]
    for m in models_[1:]:  # compare like with like when the other run covers fewer files
        names = {r["file"] for r in runs[m]["files"]}
        if len(names) < len(runs[base_model]["files"]):
            sub = [r for r in runs[base_model]["files"] if r["file"] in names]
            price = runs[base_model]["metrics"]["cost_per_invoice_usd"]["assumed_usd_per_mtok"]
            key = f"{base_model} (same {len(sub)} files)"
            runs[key] = {**runs[base_model], "metrics": metrics(sub, base_model, price),
                         "files": sub}
            models_.insert(models_.index(m), key)
    L = ["# Evaluation results", "",
         "Generated by `python scripts/run_eval.py`. Raw numbers: `eval_results.json`.", ""]
    for m in models_:
        r = runs[m]
        if m not in store["runs"]:
            continue
        L.append(f"- `{m}`: {r['metrics']['files']} files, {r['timestamp']}, "
                 f"{r['cache_misses']} fresh LLM extractions / {r['cache_hits']} cache hits, "
                 f"{r['api_retries']} API retries"
                 + (f", scans OCR'd at max side {r['ocr_settings']['max_side_px']} px,"
                    f" PDF DPI {r['ocr_settings']['pdf_dpi']}" if r.get("ocr_settings") else ""))
    L += ["", "## Summary", "", "| Metric | " + " | ".join(f"`{m}`" for m in models_) + " |",
          "|---|" + "---|" * len(models_)]

    def row(label, fn):
        L.append(f"| {label} | " + " | ".join(fn(runs[m]["metrics"]) for m in models_) + " |")

    row("Files", lambda x: str(x["files"]))
    row("Jobs failed", lambda x: str(x["jobs_failed"]))
    row("Files escalated to stronger model", lambda x: str(x["escalated_files"]))
    row("Text extraction success", lambda x: _fmt(x["ocr"]["text_extraction_success_pct"], "%"))
    row("Avg OCR confidence (all)", lambda x: _fmt(x["ocr"]["overall"]["avg_ocr_confidence"]))
    row("Avg OCR confidence, digital", lambda x: _fmt(x["ocr"]["digital"]["avg_ocr_confidence"])
        + f" ({x['ocr']['digital']['files']} files)")
    row("Avg OCR confidence, scanned", lambda x: _fmt(x["ocr"]["scanned"]["avg_ocr_confidence"])
        + f" ({x['ocr']['scanned']['files']} files)")
    row("Key-token recall, digital / scanned",
        lambda x: f"{_fmt(x['ocr']['digital']['avg_key_token_recall'])} / "
                  f"{_fmt(x['ocr']['scanned']['avg_key_token_recall'])}")
    row("Field accuracy (overall)", lambda x: _fmt(x["field_accuracy"]["overall_pct"], "%"))
    row("Decision accuracy", lambda x: f"{_fmt(x['decision_accuracy_pct'], '%')} "
                                       f"({x['decisions_correct']}/{x['files']})")
    row("Flagged class precision", lambda x: _fmt(x["flagged_class"]["precision_pct"], "%"))
    row("Flagged class recall", lambda x: _fmt(x["flagged_class"]["recall_pct"], "%"))
    row("**False approves**", lambda x: f"**{x['flagged_class']['false_approves']}**")
    row("False flags (good invoice held)", lambda x: str(x["flagged_class"]["false_flags"]))
    row("Latency avg / p95 (ms)", lambda x: f"{_fmt(x['latency_ms']['avg'])} / "
                                            f"{_fmt(x['latency_ms']['p95'])}")
    row("Tokens per invoice (avg)", lambda x: _fmt(x["tokens_per_invoice"]["avg"]))
    row("Cost per invoice, free tier", lambda x: "$0")
    row("Cost per invoice, paid (est.)",
        lambda x: f"${x['cost_per_invoice_usd']['paid_estimate']:.5f} "
                  f"(@ ${x['cost_per_invoice_usd']['assumed_usd_per_mtok']}/Mtok assumed)")
    L += ["", "Latency = text extraction + extraction LLM call + agent loop (excludes throttle "
          "delays and retry waits; cached runs reuse the originally measured LLM/agent times). "
          "Paid cost uses an assumed blended price per million tokens (Gemini returns only a "
          "total token count here); the prices are placeholders, check current pricing.", ""]

    L += ["## Per-field accuracy", "",
          "| Field | " + " | ".join(f"`{m}`" for m in models_) + " |",
          "|---|" + "---|" * len(models_)]
    for f in FIELDS:
        cells = []
        for m in models_:
            p = runs[m]["metrics"]["field_accuracy"]["per_field"][f]
            cells.append(f"{_fmt(p['pct'], '%')} ({p['correct']}/{p['total']})")
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L.append("")

    for m in models_:
        cm = runs[m]["metrics"]["confusion_matrix"]
        cols = [*DECISIONS, "error"]
        L += [f"## Decision confusion matrix: `{m}`", "",
              "Rows = expected, columns = actual.", "",
              "| expected \\ actual | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for e in DECISIONS:
            L.append(f"| **{e}** | " + " | ".join(str(cm[e][a]) for a in cols) + " |")
        fc = runs[m]["metrics"]["flagged_class"]
        L += ["", f"False approves: {fc['false_approves']} "
                  f"{fc['false_approve_files'] or ''}; false flags: {fc['false_flags']} "
                  f"{fc['false_flag_files'] or ''}", ""]

    for m in models_:
        fails = [(r, likely_reason(r)) for r in runs[m]["files"] if likely_reason(r)]
        L += [f"## Failures: `{m}`", ""]
        if not fails:
            L += ["None.", ""]
            continue
        L += ["| File | Category | Expected → actual | Wrong fields | Likely reason |",
              "|---|---|---|---|---|"]
        for r, why in fails:
            bad = ", ".join(f for f, ok in r["field_correct"].items() if not ok) or "-"
            L.append(f"| {r['file']} | {r['category']} | {r['expected_decision']} → "
                     f"{r['actual_decision']} | {bad} | {why} |")
        L.append("")

    L += ["## OCR settings (scanned files)", "",
          "Scans were re-run after the Phase 10 memory cut (cache key includes the OCR settings). "
          "Digital PDFs use embedded text, so these settings do not affect them.", "",
          "| | Phase 8 | Phase 10 (current) |", "|---|---|---|",
          "| Max image side / PDF render DPI | 2000 px / 200 DPI | 1280 px / 150 DPI |",
          "| Peak RAM while OCR'ing scans (Windows) | ~705 MB | ~470 MB |",
          "| Scanned decisions correct | 8/8 | 7/8 |",
          "| Avg OCR confidence (scanned) | 0.9705 | 0.9712 |",
          "| Key-token recall (scanned) | 0.9524 | 0.9048 |",
          "| `invoice_number` accuracy (all 30 files) | 100% | 96.7% (29/30) |", "",
          "Cost of the smaller OCR size: `inv_02_scan.jpg` lost its invoice number, so a clean "
          "invoice was held for manual review (a false flag, not a false approve). Raise "
          "`OCR_MAX_SIDE_LEN` if RAM allows; one scan failing is a sample of one.", "",
          "## Limitations", "",
          "- **Model comparison covers fewer files.** The free tier allows 20 requests/day/model "
          "for `gemini-3.5-flash` (about 7 invoices at 3 calls each), so the escalation run is "
          "limited to the files it could finish; the default model is re-scored on the same "
          "files for a like-for-like column. Re-run `--model gemini-3.5-flash` on later days: "
          "cached results are kept, so it resumes where quota ran out.",
          "- **Escalation latency** includes server-side slowness under quota pressure; "
          "treat it as indicative only.",
          "- **Synthetic data.** All 33 files come from one generator (`generate_invoices.py`): 4 "
          "clean layouts, fictional vendors, perfectly regular tables. Real invoices are messier; "
          "treat these numbers as an upper bound, not an expected production rate.",
          "- **Small sample.** 25 digital + 8 scanned files; most failure categories have 2-3 "
          "examples, so one file moves a percentage by 3-12 points. No confidence intervals; "
          "differences between models of a file or two are noise.",
          "- **Scans are synthetic too** (rasterized, rotated, blurred): easier than phone photos.",
          "- **Decision ground truth was derived from the same validators and router being "
          "tested** (checked offline), so decision accuracy mostly measures extraction fidelity, "
          "not whether the rules are right.",
          "- **Duplicate cases depend on processing order** and on a fresh DB per set.",
          "- **Agent LLM loop on cached runs** is replaced by the deterministic backstop (tools "
          "take no model-supplied numbers, so verdicts match); token and time figures are the "
          "originally measured ones.",
          "- **Cost figures:** free tier is $0; the paid estimate uses assumed blended prices, "
          "not verified list prices.",
          "- SAP posting is stubbed in the harness (HTTP call replaced), everything else is the "
          "real pipeline.", ""]
    REPORT.write_text("\n".join(L), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", help="Gemini model (default: DEFAULT_MODEL from .env)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N files")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="min seconds between Gemini API calls (free-tier RPM)")
    ap.add_argument("--retries", type=int, default=5, help="retries per call on 429/5xx")
    ap.add_argument("--price", type=float, help="assumed blended USD per 1M tokens")
    ap.add_argument("--scanned-only", action="store_true",
                    help="run only the scanned files and merge them into the stored run")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    if args.report_only:
        write_report(json.loads(RESULTS.read_text(encoding="utf-8")))
        return
    run_eval(args)


if __name__ == "__main__":
    main()
