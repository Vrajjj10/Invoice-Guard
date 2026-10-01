# Synthetic test data

Everything here is fictional: vendor names, GSTINs (correct format + valid mod-36 check
character, except the deliberately broken ones) and amounts. Regenerate (byte-identical) with:

```
python scripts/generate_invoices.py [--seed 7] [--out data/invoices]
```

| Path | Contents |
|---|---|
| `invoices/inv_01..25.pdf` | 25 digital documents from 8 vendors, 4 layouts (`classic`, `band`, `boxed`, `ledger`), mixed date/number formats |
| `invoices/scanned/*_scan.{png,jpg}` | 8 scanned-style copies (rasterized, rotated ±1.4°, blur, noise, uneven light, JPEG artifacts) |
| `invoices/ground_truth.json` | per file: expected fields, `decision` (approve / manual_review / reject), `reason_code`, `reason` |
| `vendors.json` | vendor master (8 test vendors + 1 blocked vendor from earlier phases) |

Mix of the 25 PDFs: 12 clean (7 intra-state CGST+SGST, 5 inter-state IGST) · 3 wrong totals
(grand total off, transposed digits, line amount mismatch) · 2 wrong tax math (off-slab 14%,
IGST on intra-state) · 2 invalid GSTIN (bad checksum, bad format) · 2 exact duplicates (same
vendor/number/total, different layout so file bytes differ) · 2 unusually high amounts
(~10-12x vendor norm) · 2 non-invoices (bank-detail letter, petty-cash memo).

Notes
- Buyer is always `Dimexon Components LLP` (Maharashtra, state 27).
- `scanned/` covers 4 clean, 1 wrong total, 1 invalid GSTIN, 1 high amount, 1 non-invoice.
  A scan has the same duplicate hash as its original, so evaluate the digital and scanned sets
  against **separate fresh DBs**.
- Expected decisions were checked offline against the real validators, vendor master and
  anomaly model with a confident extraction (0.95); real runs also depend on LLM/OCR accuracy.
- Anomaly cases need `python scripts/train_anomaly.py` (the test vendors are in its list).
