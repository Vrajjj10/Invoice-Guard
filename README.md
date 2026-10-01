# InvoiceGuard

[![CI](https://github.com/Vrajjj10/Invoice-Guard/actions/workflows/ci.yml/badge.svg)](https://github.com/Vrajjj10/Invoice-Guard/actions/workflows/ci.yml)

AI accounts-payable automation agent for B2B invoices (prototype): OCR/text extraction, one LLM
call for fields, deterministic validators, tool-calling agent, routing, mock SAP posting.

```bash
pip install -r requirements-dev.txt
cp .env.example .env              # add GEMINI_API_KEY
uvicorn app.main:app --reload     # docs at /docs
pytest -q                         # no API key or network needed
docker build -t invoiceguard . && docker run -p 8000:8000 -e GEMINI_API_KEY=... invoiceguard
```

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
