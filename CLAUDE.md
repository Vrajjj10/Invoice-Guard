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
| `data/samples/` | Single sample invoice (digital + scanned) from `make_sample.py` |
| `data/invoices/` | Phase 7 test set: 25 PDFs, 8 scans, `ground_truth.json` (see `data/README.md`) |
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
| 3 LLM extraction | done | One Gemini call → `InvoiceFields` (Pydantic), escalation to stronger model on low confidence / bad JSON |
| 4 Validators + agent | done | Pure validators (line items, subtotal, GST rate, CGST/SGST vs IGST, grand total, GSTIN regex + mod-36 checksum, duplicate hash), provider-agnostic tool-calling interface + Gemini impl, agent loop with iteration cap + deterministic backstop, vendor master JSON. Verified live on gemini-3.5-flash-lite: all 6 tools called in 1 iteration (~1.8k tokens), duplicate caught across digital vs scanned PDF |
| 5 Anomaly + router | done | Per-vendor IsolationForest (log amount) trained by `scripts/train_anomaly.py` on seeded synthetic history (16 vendors); real `flag_anomaly` with cold-start rule (amount > 5x overall median) for vendors with < 20 invoices; router → approve / manual_review / reject with config thresholds; `audit_log` table; console/Slack alert for review + reject |
| 6 Mock SAP + review queue | done | `/mock-sap/supplier-invoices` (validated, stored in `sap_documents`, fake 10-digit doc no. `51########`); `sap/payload.py` builder; `sap/client.py` (httpx, `SAP_BASE_URL`, retry w/ backoff, no retry on 4xx); `sap/posting.py` `post_job`; approved jobs auto-post in pipeline, SAP failure → `manual_review` with reason; `/reviews` queue (list/detail/approve/correct/reject) |
| 7 Test data | done | `scripts/generate_invoices.py` (seeded, byte-reproducible): 25 PDFs / 8 vendors / 4 layouts, 8 scanned PNG/JPG, `data/invoices/ground_truth.json` (fields + decision + reason per file); 6 new vendors added to `vendors.json` and `train_anomaly.py`; offline check: 25/25 expected decisions match validators + router |
| 8 Eval harness | done | `scripts/run_eval.py`: runs `data/invoices/*` through the real `process_job` (SAP post stubbed), throttled + retried Gemini calls, disk cache by file hash + model (`data/eval/cache`, git-ignored), `--limit/--model/--delay/--report-only`; writes `eval_results.json` + `EVAL.md` |
| 9–10 | todo | CI/Docker → deploy |

### Endpoints so far

- `GET /health` → `{"status":"ok",...}`
- `POST /invoices/upload` (multipart `file`) → `202 {job_id, status, cached, file_hash}`;
  `400` empty, `413` > 10 MB, `415` not PDF/PNG/JPEG
- `GET /jobs/{job_id}` → status, `extraction_method` (`text`/`ocr`/`mixed`), `ocr_confidence`,
  `page_count`, `extraction_ms`, `extracted_text`, `error`, `fields` (LLM), `checks`
  (`results` per tool key, `backfilled`, `error`), `agent_notes`, `agent_iterations`,
  `agent_tokens`; `404` if unknown

### Phase 6 endpoints

- `POST /mock-sap/supplier-invoices` → `201 {document_number, fiscal_year, company_code, status}`;
  `422` on missing/empty required field; idempotent per company+vendor+reference. `GET /mock-sap/supplier-invoices/{doc}`
- `GET /reviews?status=pending|approved|rejected` → queue (jobs that ever hit manual_review)
- `GET /reviews/{id}` → `fields` (original), `corrected_fields`, `corrections`, `reasons`, `checks`, `sap_doc_number`, `sap_error`, `audit_trail`
- `POST /reviews/{id}/approve` → posts to SAP (`502` + stays pending on failure); `/correct` body `{"fields": {...partial}}`
  → stores original vs corrected, re-runs validators (no LLM), returns re-route preview, stays pending; `/reject` body `{"reason"}`.
  `409` unless pending
- Config: `SAP_BASE_URL`, `SAP_COMPANY_CODE`, `SAP_GL_ACCOUNT`, `SAP_TAX_CODE`, `SAP_MAX_ATTEMPTS` (3), `SAP_RETRY_DELAY` (0.5 s, doubled)

### Key files (Phase 5)

- `app/ml/anomaly.py` — `flag_anomaly(gstin, name, amount)` → `{ok, anomaly, score, method: model|rule|none, reason}`;
  loads `models/anomaly.joblib` (bundle: per-vendor models, counts, overall median). Untrained → `ok: None`
- `scripts/train_anomaly.py` — seeded synthetic history + training; `python scripts/train_anomaly.py`
- `app/routing/router.py` — `route(fields, agent_results)` → `RouteResult(decision, reasons, confidence)`;
  `record_decision` sets `jobs.decision` + adds `audit_log` row
- `app/routing/alerts.py` — `send_alert`: log warning; POST to `SLACK_WEBHOOK_URL` if set (never raises)
- Config: `APPROVE_MIN_CONFIDENCE` (0.85), `REJECT_BELOW_CONFIDENCE` (0.40), `ANOMALY_MODEL_PATH`,
  `anomaly_min_history` (20), `anomaly_cold_start_multiple` (5.0)
- `GET /jobs/{id}` now also returns `decision` and `decision_reasons`

### Key files (Phases 3–4)

- `app/llm/client.py` — `extract_invoice`: single extraction call + escalation
- `app/llm/provider.py` — `ToolChatProvider`/`ChatSession` protocol, `ToolSpec`/`ToolCall`/
  `ToolResult`/`ChatTurn`; `GeminiProvider` (manual function calling, AFC disabled)
- `app/validators/` — `gstin.py`, `totals.py`, `duplicate.py`: pure functions, return
  `{ok: True|False|None, detail}` (`None` = not checkable, missing data)
- `app/agent/tools.py` — `TOOL_SPECS`, `run_tool` dispatcher, `find_vendor`, `check_duplicate`
  (DB lookup on `jobs.dup_hash`); `flag_anomaly` is a stub until Phase 5
- `app/agent/loop.py` — `run_agent(ctx, provider)`; `MAX_ITERATIONS = 6`
- `data/vendors.json` — vendor master (`VENDORS_PATH`)

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
python scripts\train_anomaly.py       # writes models/anomaly.joblib (needed for real anomaly scores)
python scripts\make_sample.py         # writes data/samples/sample_*.{pdf,png}
python scripts\generate_invoices.py   # writes data/invoices/ (25 PDFs, scanned/, ground_truth.json)
python scriptsun_eval.py [--model gemini-3.5-flash] [--limit N]   # eval -> EVAL.md, eval_results.json
python scripts\show_ocr.py data\samples\sample_scanned.png
pytest -q                             # tests (~22 s; OCR slow; Gemini is mocked, no key needed)
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
- **Agent tools take no numbers from the model.** Tools read the extracted `InvoiceFields` from
  `ToolContext`; model args only pick *what* to check (`party`, lookup keys). The model's final
  text is stored as `agent_notes` only — verdicts are the tool outputs.
- **Deterministic backstop:** after the loop, any required tool the model skipped (or all of
  them, if the LLM call failed) is run in code and listed in `checks.backfilled`.
- **Tolerances:** ±₹1.00 for line/subtotal/grand-total; CGST must equal SGST within ₹0.01;
  effective GST rate must be within 0.1 pp of a slab {0, 0.25, 3, 5, 12, 18, 28, 40}% (mixed-rate
  invoices will fail `tax_rate` — prototype limitation).
- **Intra/inter-state** = first 2 GSTIN digits of vendor vs buyer. Unknown buyer state → split
  check skipped unless both CGST/SGST and IGST are charged.
- **Duplicate hash** = SHA-256 of `GSTIN (else A-Z0-9 name) | A-Z0-9 invoice no | total:.2f`;
  stored on `jobs.dup_hash`; matches exclude the current job and failed jobs. Identical file
  bytes never get here (file cache returns the old job).
- **Routing rules:** reject = not an invoice, duplicate, blocked/inactive vendor, confidence < 0.40.
  manual_review = confidence < 0.85, unknown vendor, name/GSTIN mismatch, any failed/errored/uncheckable
  totals or vendor-GSTIN check, anomaly flagged. Otherwise approve. Buyer-GSTIN or anomaly "not checkable"
  (`ok: None`) does not block. Reject reasons also list review reasons.
- **Anomaly:** one IsolationForest per vendor on `log(amount)` (contamination 0.02); score = `-score_samples`.
  Prototype limitation: trained on synthetic data, amount-only, no retraining from real jobs.
- **Review/SAP:** `jobs.review_status` (pending/approved/rejected) is set when a job routes to manual_review;
  `fields_json` is never overwritten — corrections go to `corrected_fields_json` (+ `corrections_json` diff) and SAP
  posts the corrected values. Reviewer approve is a human override: validators are not re-enforced at approve.
  Audit decisions: approve / manual_review / reject / `corrected`. Payload vendor = GSTIN (else name); posting date = today.
  Delete local `invoiceguard.db` after pulling Phase 6 (new columns, no migrations).
- **Agent model:** `DEFAULT_MODEL` (no escalation for the agent loop).
- Lint rule B008 is allowed for `fastapi.Depends/File/Query` (standard FastAPI idiom).
- **Test set (Phase 7):** ground truth is keyed by filename; scanned files carry `scan_of`. Duplicates are
  re-rendered in a different layout (same vendor/number/total) so the file-hash cache doesn't hide them.
  Digital and scanned sets must be run on separate fresh DBs (scan = duplicate of its original).
  Test-vendor GSTINs are computed with `gstin_check_char`; `data/vendors.json` and the `train_anomaly.py`
  list must stay in sync with `VENDORS` in the generator (retrain after changing).
- **Eval (Phase 8):** digital and scanned sets run on separate fresh DBs (`data/eval/eval.db`). On a cache hit the agent LLM is replaced by the deterministic backstop (same verdicts). Free tier allows only 20 requests/day for `gemini-3.5-flash`, so the escalation run is partial (8 files; resumes from cache on later days). Paid cost uses assumed blended prices (`DEFAULT_PRICE` in the script). Default-model result: 33/33 decisions, 0 false approves, 100% field accuracy on synthetic data (upper bound).

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
- **Gemini function calling:** append the model's `candidates[0].content` to history as-is
  (it carries thought signatures Gemini 3 requires), and echo `FunctionCall.id` back in the
  `FunctionResponse`. Response dicts must be JSON-serializable.
- **Tests:** `conftest.py` has an autouse fixture that replaces `loop.get_provider` with a
  silent fake, so the agent never hits Gemini; override it per test with `monkeypatch`.
- **Scanned-sample OCR test is mildly flaky** (random noise/rotation in `make_sample`), e.g.
  `15,399.00` read as `15.399.00`. Rerun; seed it if it becomes annoying.
- **Windows:** activation may need `Set-ExecutionPolicy -Scope Process RemoteSigned`;
  `.gitattributes` forces LF so files match Linux (Docker/CI/Render). Use `curl.exe`, not
  PowerShell's `curl` alias.
