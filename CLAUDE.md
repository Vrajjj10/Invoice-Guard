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
| `app/llm/` | Gemini client (google-genai), prompts, Pydantic schema, model escalation |
| `app/validators/` | Deterministic checks: totals, tax, GSTIN, duplicates |
| `app/agent/` | Tool definitions + tool-calling agent loop |
| `app/ml/` | IsolationForest anomaly detector (inference) |
| `app/routing/` | Decision router, audit logging, alert stub |
| `app/sap/` | SAP payload builder (separate) + mock SAP route |
| `app/db/` | SQLAlchemy models/session (SQLite) |
| `scripts/` | Training, synthetic data generation, eval harness |
| `tests/` | pytest; LLM client is always mocked |
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
- **Models:** default `gemini-3.5-flash-lite`; escalate to `gemini-3.5-flash` only for
  low-confidence results. Keep system prompt + schema short and stable (Gemini implicit caching).
- **One LLM call** for extraction + confidence. Strict JSON, validated with Pydantic. Short
  reason strings.
- **Tool calling:** tools `check_duplicate`, `validate_totals`, `validate_gstin`,
  `lookup_vendor`, `flag_anomaly`, with a proper agent loop (handle function_call ->
  function_response until no more calls, with an iteration cap).
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
- Tests never call the real Gemini API (mock the client).
- Be honest about limitations; this is a prototype.

## Progress

| Phase | Status | What it built |
|---|---|---|
| 1 Setup | done | Folder skeleton, venv, split requirements (runtime vs dev), typed `Settings` from `.env`, `/health`, ruff + pytest config, git repo with LF line endings |
| 2 Upload + extraction | done | `POST /invoices/upload`, `GET /jobs/{id}`, `jobs` table, SHA-256 file cache, BackgroundTasks pipeline, PyMuPDF text + RapidOCR fallback, sample generator + OCR viewer script |
| 3–10 | todo | LLM extraction → validators/agent → anomaly + router → SAP/review → data → eval → CI/Docker → deploy |

### Endpoints so far

- `GET /health` → `{"status":"ok",...}`
- `POST /invoices/upload` (multipart `file`) → `202 {job_id, status, cached, file_hash}`;
  `400` empty, `413` > 10 MB, `415` not PDF/PNG/JPEG
- `GET /jobs/{job_id}` → status, `extraction_method` (`text`/`ocr`/`mixed`), `ocr_confidence`,
  `page_count`, `extraction_ms`, `extracted_text`, `error`; `404` if unknown

### Key files (Phases 1–2)

- `app/extraction/text.py` — `detect_file_type`, `clean_text`, `extract_text` (+ row regrouping)
- `app/extraction/ocr.py` — RapidOCR singleton (`load_engine`) and `run_ocr`
- `app/pipeline.py` — `process_job(job_id)`: the background task; later phases extend it
- `app/api/invoices.py` — upload + job routes, cache lookup
- `app/db/session.py`, `app/db/models.py` — engine/session, `Job` table
- `scripts/make_sample.py` — one GST invoice as digital PDF + scanned-style PDF/PNG
- `scripts/show_ocr.py` — print extraction output for files

## How to run

```powershell
cd invoiceguard
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned   # if activation is blocked
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env                # then fill GEMINI_API_KEY

uvicorn app.main:app --reload         # API at http://127.0.0.1:8000, docs at /docs
python scripts\make_sample.py         # writes data/samples/sample_*.{pdf,png}
python scripts\show_ocr.py data\samples\sample_scanned.png
pytest -q                             # tests (~17 s; OCR slow; Gemini is mocked, no key needed)
ruff check .                          # lint

# after uploading, poll the job; `fields` holds the Gemini-extracted invoice
curl.exe http://127.0.0.1:8000/jobs/<job_id>

# upload from the shell
curl.exe -F "file=@data/samples/sample_scanned.pdf" http://127.0.0.1:8000/invoices/upload
```

## Decisions

- **Scanned-page threshold:** a PDF page with < 30 chars of embedded text (`MIN_PAGE_CHARS`) is
  rendered at 200 DPI and OCR'd. Limits: 10 pages, 12 000 chars of text to LLM, 10 MB upload.
- **Row regrouping:** both PyMuPDF words and RapidOCR boxes are regrouped into visual rows by
  vertical centre, then sorted left→right. A horizontal gap > 15 pt becomes a double space
  (column separator). This keeps table rows like `Steel Bolts M8  7318  500  4.50  2,250.00`
  on one line for the LLM.
- **`ocr_confidence` meaning:** mean RapidOCR line score over OCR'd lines; `1.0` when all
  pages had embedded text; `0.0` when OCR ran but found nothing.
- **File type by magic bytes**, not filename or content-type header.
- **Cache:** an upload whose SHA-256 matches an existing non-failed job returns that job
  (`cached: true`); failed jobs are re-processed on re-upload. Files stored as
  `data/uploads/<sha256>.<ext>`.
- **Gemini models** get retired for new users without warning (2.5-flash-lite 404'd); list with `client.models.list()`. Keep the `genai.Client` in a variable/global, or it is closed on GC.
- **Schema via `create_all`, no migrations** (prototype). When a phase adds columns, delete
  `invoiceguard.db` locally.
- Lint rule B008 is allowed for `fastapi.Depends/File/Query` (standard FastAPI idiom).

## Gotchas

- **PyMuPDF `get_text("text")` puts every table cell on its own line** — that's why we use
  `get_text("words")` + row regrouping instead.
- **`clean_text` must not collapse double spaces**; they're column separators. It collapses 3+.
- **RapidOCR drops spaces between words** on the default model ("ShreeGaneshTraders");
  numbers, GSTINs and invoice numbers survive. Leave it to the LLM; don't "fix" with regex.
- **RapidOCR is slow on CPU:** ~4–7 s per scanned page at 200 DPI. First option if too slow:
  lower `RENDER_DPI` to 150.
- **RapidOCR wants BGR `ndarray`s** (OpenCV convention); PyMuPDF pixmaps are RGB(A), so
  `_pixmap_to_array` drops alpha and flips channels. Image uploads are passed as raw bytes.
- **`numpy<2.0` pin** is required by rapidocr-onnxruntime/onnxruntime.
- **SQLite needs `check_same_thread=False`** because BackgroundTasks run in a threadpool.
  Background tasks open their own `SessionLocal()`; never pass the request's session.
- **Tests:** `tests/conftest.py` sets `DATABASE_URL`/`UPLOAD_DIR` to a temp dir *before* app
  import (settings are `lru_cache`d and the engine is module-level). Use
  `with TestClient(app)` so the lifespan (DB init + OCR load) runs. TestClient executes
  background tasks before returning, so jobs are already `done` in tests.
- **Windows:** activation may need `Set-ExecutionPolicy -Scope Process RemoteSigned`;
  `.gitattributes` forces LF so files match Linux (Docker/CI/Render). Use `curl.exe`, not
  PowerShell's `curl` alias.
