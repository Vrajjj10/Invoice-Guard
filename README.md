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

See `CLAUDE.md` for architecture and design rules.
