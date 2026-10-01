# InvoiceGuard

[![CI](https://github.com/Vrajjj10/Invoice-Guard/actions/workflows/ci.yml/badge.svg)](https://github.com/Vrajjj10/Invoice-Guard/actions/workflows/ci.yml)

**Live demo: https://invoice-guard-df71.onrender.com/** (free tier: the first load can take about a minute; the demo key is needed to run invoices)

AI accounts-payable automation agent for B2B invoices (prototype): OCR/text extraction, one LLM
call for fields, deterministic validators, tool-calling agent, routing, mock SAP posting.

```bash
pip install -r requirements-dev.txt
cp .env.example .env              # add GEMINI_API_KEY
uvicorn app.main:app --reload     # docs at /docs
pytest -q                         # no API key or network needed
docker build -t invoiceguard . && docker run -p 8000:8000 -e GEMINI_API_KEY=... invoiceguard
```

## Why use it

Numbers come from `EVAL.md` (33 **synthetic** invoices, `gemini-3.5-flash-lite`); real invoices will be messier, so treat them as a best case.

| | Manual entry | InvoiceGuard |
|---|---|---|
| Time per invoice | minutes | ~6.6 s average, ~10 s p95 |
| Cost per invoice | staff time | ~$0.0006 (~INR 0.05) on paid Gemini; est., assumed price; $0 on free tier |
| 1,000 invoices a day | several full-time staff | ~INR 1,600 to 2,400 a month in AI cost (est.) |
| Math, GST and GSTIN checks | by eye, easy to miss | deterministic code, every invoice |
| Duplicate detection | memory or spreadsheet | vendor + number + total hash, even across layouts |
| Audit trail | scattered | every decision logged with reasons and confidence |

Measured: 99.7% field accuracy, 32/33 correct decisions, **0 false approves**, 1 false flag. Repeat uploads and reviewer re-validation use 0 tokens. About 2,400 tokens per invoice; ~3,650 when escalated to the stronger model.

## Using the web UI

Open `/` (locally http://127.0.0.1:8000, or the Render URL). Enter the demo key in the box at the top (kept in `sessionStorage` only; not needed locally when `ENVIRONMENT=dev`).

**How it works:** upload -> text extraction (OCR reads scans; digital PDFs use embedded text) -> AI extraction (Gemini turns text into fields + confidence) -> validation (plain code checks totals, tax, GSTIN, duplicates, unusual amounts) -> decision (approve / needs review / reject) -> SAP (mock, approved only).

| Feature | What it does |
|---|---|
| Upload box | Drag and drop or click; PDF, PNG or JPG, max 5 MB |
| Overview | Invoices processed, auto-approved %, flagged, average processing time |
| Try a sample | One click runs a bundled demo invoice |
| Live pipeline | Six stages light up as the job runs, with per-stage times |
| Decision banner | Green approved (with SAP doc no.), amber needs review (reason chips), red rejected; confidence ring |
| Preview + fields | Your file on the left; extracted fields with estimated confidence bars on the right (amber = low) |
| Audit timeline | Decisions and reasons for the invoice just processed |
| Review queue | Flagged invoices: approve, reject, or edit fields and re-validate |
| Audit log | Latest decisions across all invoices |

**Demo walkthrough** (run in this order; the duplicate needs the clean one first):

1. **Clean invoice** -> green "Approved and posted to SAP (doc no. 51...)"; fields table filled.
2. **Intra-state GST** -> green approved.
3. **Wrong total** -> amber "Needs review" with a "Total mismatch" chip; a card appears in the review queue.
4. In the queue click **Edit**, fix **Grand total** (subtotal + tax), **Save and re-validate** -> "approve", then **Approve and post** -> toast with a SAP doc no.
5. **Duplicate** -> red "Rejected" with a "Duplicate" chip.
6. **Not an invoice** -> red "Rejected", "No invoice fields".

Notes: approving posts to the app's own mock SAP (`/mock-sap`, stored in SQLite); set `SAP_BASE_URL` for a real one. On Render's free tier the first load can take about a minute ("service is waking up") and all data resets on restart. Prototype on synthetic data; tax-inclusive and mixed-rate invoices are flagged for review as false alarms.

## Deploy (Render free tier)

`render.yaml` + `Dockerfile`: the container listens on `$PORT`, trains the anomaly model at image
build, and creates the SQLite DB on startup (ephemeral disk: data resets on redeploy/sleep).
Set `GEMINI_API_KEY` and `DEMO_API_KEY` as env vars in Render.

- `X-API-Key: <DEMO_API_KEY>` is required on `POST /invoices/upload` and all `/reviews*` routes;
  `GET /jobs/{id}` and `/mock-sap/*` need it too; only `/health` and the docs are open. The internal SAP client sends the key when `SAP_BASE_URL` is localhost. With `ENVIRONMENT` other than
  `dev` (the image default is `production`) a missing `DEMO_API_KEY` makes those routes return 503.
- Upload limits: 5 MB (`MAX_UPLOAD_BYTES`), 10 uploads/min per IP (`UPLOAD_RATE_LIMIT`; in-memory,
  per process). Demo files: `data/samples/`.
- Memory: RapidOCR loads once at startup and runs one page at a time. Images are downscaled to 1280 px
  (`OCR_MAX_SIDE_LEN`) and PDF pages render at 150 DPI. Measured on Windows/Python 3.11 (not Linux/Render, so treat as approximate): ~180 MB idle,
  **peak ~470 MB** when OCR'ing scans (was ~705 MB at 2000 px / 200 DPI). That is close to Render's
  512 MB limit, so keep the demo to a few scans at a time; text PDFs barely move RAM. Lower
  `OCR_MAX_SIDE_LEN` if the instance gets OOM-killed. At 1280 px/150 DPI the eval lost one scan (7/8 scanned decisions correct, was 8/8); see `EVAL.md`.

See `CLAUDE.md` for architecture and design rules.
