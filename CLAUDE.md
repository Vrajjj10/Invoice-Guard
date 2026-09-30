# InvoiceGuard — project guide

AI accounts-payable automation agent for B2B (prototype). Built in phases; the owner is an
ML student learning FastAPI/OCR/Docker/CI — explain new concepts briefly before using them.

## Pipeline

```
upload (PDF/image) -> file-hash cache check -> background job (returns job_id immediately)
  -> text extraction (PyMuPDF text, else PyMuPDF render + RapidOCR) -> clean/trim text
  -> ONE LLM call: fields + confidence (strict JSON, Pydantic-validated)
  -> agent loop with tools: check_duplicate, validate_totals, validate_gstin,
     lookup_vendor, flag_anomaly
  -> router: approve -> mock SAP post | manual review (+ alert) | reject (non-invoice)
  -> audit log row for every decision (reason + confidence)
```

## Layout

| Path | Responsibility |
|---|---|
| `app/main.py` | FastAPI app, startup (load OCR engine once), routers |
| `app/config.py` | Typed settings from `.env` (pydantic-settings) |
| `app/api/` | HTTP routes: upload, jobs, review queue |
| `app/extraction/` | PyMuPDF + RapidOCR text extraction, OCR confidence |
| `app/llm/` | Anthropic client, prompts, Pydantic schema, model escalation |
| `app/validators/` | Deterministic checks: totals, tax, GSTIN, duplicates |
| `app/agent/` | Tool definitions + tool-calling agent loop |
| `app/ml/` | IsolationForest anomaly detector (inference) |
| `app/routing/` | Decision router, audit logging, alert stub |
| `app/sap/` | SAP payload builder (separate) + mock SAP route |
| `app/db/` | SQLAlchemy models/session (SQLite) |
| `scripts/` | Training, synthetic data generation, eval harness |
| `tests/` | pytest; Claude client is always mocked |
| `data/samples/` | Synthetic invoices + ground-truth JSON |
| `models/` | Trained model artifacts (git-ignored) |

## Design rules (do not violate)

- **LLM for understanding, deterministic code for verification.** The LLM never decides
  whether math/GSTIN/duplicates are correct — code does.
- **Text extraction:** PDFs first use PyMuPDF embedded text; only pages with little/no text
  are rendered to images with PyMuPDF (no Poppler) and run through RapidOCR. Images go
  straight to RapidOCR. Return text + average OCR confidence. RapidOCR engine is loaded
  **once at startup**, never per request.
- **Send TEXT to the LLM**, cleaned and trimmed. Fall back to sending the image only when OCR
  confidence is low.
- **Models:** default `claude-haiku-4-5-20251001`; escalate to `claude-sonnet-5-5` only for
  low-confidence results. Use prompt caching on system prompt + schema.
- **One LLM call** for extraction + confidence. Strict JSON, validated with Pydantic. Short
  reason strings.
- **Tool calling:** tools `check_duplicate`, `validate_totals`, `validate_gstin`,
  `lookup_vendor`, `flag_anomaly`, with a proper agent loop (handle `tool_use` ->
  `tool_result` until `end_turn`, with an iteration cap).
- **Code checks:** line items sum to subtotal, tax math, grand total, GSTIN regex + checksum,
  CGST/SGST (intra-state) vs IGST (inter-state) logic, duplicate hash of
  vendor + invoice number + amount.
- **ML:** IsolationForest on vendor/amount history, trained by a script, used by
  `flag_anomaly`.
- **Caching & async:** cache results by file SHA-256. Process uploads via FastAPI
  BackgroundTasks; return a job ID immediately.
- **Audit:** every decision is written to the audit log table with reason + confidence.
- **SAP:** mock FastAPI route mimicking a supplier-invoice payload; payload builder kept
  separate so a real URL can be swapped in via `SAP_BASE_URL`.

## Working rules

- Small steps; run code and fix errors before moving on.
- Never hardcode secrets — use `.env` (git-ignored) and `.env.example`.
- Tests never call the real Anthropic API (mock the client).
- Be honest about limitations; this is a prototype.

## Commands

```powershell
.\.venv\Scripts\Activate.ps1          # activate venv (Windows PowerShell)
pip install -r requirements-dev.txt
uvicorn app.main:app --reload         # run API at http://127.0.0.1:8000 (docs: /docs)
pytest -q                             # run tests
ruff check .                          # lint
```
